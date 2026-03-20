"""
Memory Agent Service

Handles business logic for memory agent operations including read/write services,
health checks, and message type classification.

TODO: Refactor get_end_user_connected_config
----------------------------------------------
1. Move get_end_user_connected_config to memory_config_service.py
2. Change return type from Dict[str, Any] (with config_id string) to full MemoryConfig model
3. This will eliminate the need for callers to call load_memory_config separately
4. Update all callers to use the new unified function
"""
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

import redis
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

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
from app.core.memory.analytics.hot_memory_tags import get_interest_distribution
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.models.knowledge_model import Knowledge, KnowledgeType
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_agent_schema import Write_UserInput
from app.schemas.memory_config_schema import ConfigurationError
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_konwledges_server import (
    write_rag,
)

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
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id,
                                           success=True,
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

        # Get log file path - use project root directory
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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

    async def write_memory(
            self,
            end_user_id: str,
            messages: list[dict],
            config_id: Optional[uuid.UUID] | int,
            db: Session,
            storage_type: str,
            user_rag_memory_id: str,
            language: str = "zh"
    ) -> str:
        """
        Process write operation with config_id

        Args:
            end_user_id: Group identifier (also used as end_user_id)
            messages: Message to write
            config_id: Configuration ID from database
            db: SQLAlchemy database session
            storage_type: Storage type (neo4j or rag)
            user_rag_memory_id: User RAG memory ID
            language: 语言类型 ("zh" 中文, "en" 英文)

        Returns:
            Write operation result status

        Raises:
            ValueError: If config loading fails or write operation fails
        """
        # Resolve config_id and workspace_id
        # Always get workspace_id from end_user for fallback, even if config_id is provided
        workspace_id = None
        try:
            connected_config = get_end_user_connected_config(end_user_id, db)
            workspace_id = connected_config.get("workspace_id")
            if config_id is None:
                config_id = connected_config.get("memory_config_id")
            logger.info(f"Resolved config from end_user: config_id={config_id}, workspace_id={workspace_id}")
            if config_id is None and workspace_id is None:
                raise ValueError(f"No memory configuration found for end_user {end_user_id}. "
                                 f"Please ensure the user has a connected memory configuration.")
        except Exception as e:
            if "No memory configuration found" in str(e):
                raise  # Re-raise our specific error
            logger.error(f"Failed to get connected config for end_user {end_user_id}: {e}")
            if config_id is None:
                raise ValueError(f"Unable to determine memory configuration for end_user {end_user_id}: {e}")
            # If config_id was provided, continue without workspace_id fallback

        import time
        start_time = time.time()

        # Load configuration from database with workspace fallback
        # Use a separate database session to avoid transaction failures
        try:
            from app.db import get_db_context
            with get_db_context() as config_db:
                config_service = MemoryConfigService(config_db)
                memory_config = config_service.load_memory_config(
                    config_id=config_id,
                    workspace_id=workspace_id,
                    service_name="MemoryAgentService"
                )
            logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
        except ConfigurationError as e:
            error_msg = f"Failed to load configuration for config_id: {config_id}: {e}"
            logger.error(error_msg)

            # Log failed operation
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id,
                                           success=False, duration=duration, error=error_msg)

            raise ValueError(error_msg)

        try:
            if storage_type == "rag":
                # For RAG storage, convert messages to single string
                message_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
                await write_rag(end_user_id, message_text, user_rag_memory_id)
                return "success"
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
                    print(100 * '-')
                    print(langchain_messages)
                    print(100 * '-')
                    # 初始状态 - 包含所有必要字段
                    initial_state = {
                        "messages": langchain_messages,
                        "end_user_id": end_user_id,
                        "memory_config": memory_config,
                        "language": language
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
                    return self.writer_messages_deal(massagesstatus, start_time, end_user_id, config_id, message_text,
                                                     contents)
        except Exception as e:
            # Ensure proper error handling and logging
            error_msg = f"Write operation failed: {str(e)}"
            logger.error(error_msg)
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id,
                                           success=False, duration=duration, error=error_msg)
            raise ValueError(error_msg)

    async def read_memory(
            self,
            end_user_id: str,
            message: str,
            history: List[Dict],
            search_switch: str,
            config_id: Optional[uuid.UUID] | int,
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
        ori_message = message

        # Resolve config_id and workspace_id
        # Always get workspace_id from end_user for fallback, even if config_id is provided
        workspace_id = None
        try:
            connected_config = get_end_user_connected_config(end_user_id, db)
            workspace_id = connected_config.get("workspace_id")
            if config_id is None:
                config_id = connected_config.get("memory_config_id")
            logger.info(f"Resolved config from end_user: config_id={config_id}, workspace_id={workspace_id}")
            if config_id is None and workspace_id is None:
                raise ValueError(
                    f"No memory configuration found for end_user {end_user_id}. Please ensure the user has a connected memory configuration.")
        except Exception as e:
            if "No memory configuration found" in str(e):
                raise  # Re-raise our specific error
            logger.error(f"Failed to get connected config for end_user {end_user_id}: {e}")
            if config_id is None:
                raise ValueError(f"Unable to determine memory configuration for end_user {end_user_id}: {e}")
            # If config_id was provided, continue without workspace_id fallback

        logger.info(f"Read operation for group {end_user_id} with config_id {config_id}")

        # 导入审计日志记录器
        try:
            from app.core.memory.utils.log.audit_logger import audit_logger
        except ImportError:
            audit_logger = None

        config_load_start = time.time()
        try:
            # Use a separate database session to avoid transaction failures
            from app.db import get_db_context
            with get_db_context() as config_db:
                config_service = MemoryConfigService(config_db)
                memory_config = config_service.load_memory_config(
                    config_id=config_id,
                    workspace_id=workspace_id,
                    service_name="MemoryAgentService"
                )
            config_load_time = time.time() - config_load_start
            logger.info(f"[PERF] Configuration loaded in {config_load_time:.4f}s: {memory_config.config_name}")
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
                    from app.repositories.memory_short_repository import (
                        ShortTermMemoryRepository,
                    )

                    retrieved_content = []
                    repo = ShortTermMemoryRepository(db)

                    if str(search_switch) != "2":
                        for intermediate in _intermediate_outputs:
                            logger.debug(f"处理中间结果: {intermediate}")
                            intermediate_type = intermediate.get('type', '')

                            if intermediate_type == "search_result":
                                query = intermediate.get('query', '')
                                raw_results = intermediate.get('raw_results', {})
                                try:
                                    reranked_results = raw_results.get('reranked_results', [])
                                    statements = [statement['statement'] for statement in
                                                  reranked_results.get('statements', [])]
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
                            messages=ori_message,
                            aimessages=summary,
                            retrieved_content=retrieved_content,
                            search_switch=str(search_switch)
                        )
                        logger.info(f"成功保存短期记忆: end_user_id={end_user_id}, search_switch={search_switch}")
                    else:
                        logger.debug(
                            f"跳过保存短期记忆: summary={summary[:50] if summary else 'None'}, search_switch={search_switch}")

                except Exception as save_error:
                    # 保存失败不应该影响主流程，只记录错误
                    logger.error(f"保存短期记忆失败: {str(save_error)}", exc_info=True)

                # Log successful operation
                total_time = time.time() - start_time
                logger.info(
                    f"[PERF] read_memory completed successfully in {total_time:.4f}s (config: {config_load_time:.4f}s, graph: {graph_exec_time:.4f}s)")
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
                raise ValueError(
                    f"Message format error: Message must be a dictionary. Error message index: {idx}, type: {type(msg)}")

            if 'role' not in msg:
                logger.error(f"Validation failed: Message {idx} missing 'role' field: {msg}")
                raise ValueError(f"Message format error: Message must contain 'role' field. Error message index: {idx}")

            if 'content' not in msg:
                logger.error(f"Validation failed: Message {idx} missing 'content' field: {msg}")
                raise ValueError(
                    f"Message format error: Message must contain 'content' field. Error message index: {idx}")

            if msg['role'] not in ['user', 'assistant']:
                logger.error(f"Validation failed: Message {idx} invalid role: {msg['role']}")
                raise ValueError(f"Role must be 'user' or 'assistant', got: {msg['role']}. Message index: {idx}")

            if not msg['content'] or not msg['content'].strip():
                logger.error(f"Validation failed: Message {idx} content is empty")
                raise ValueError(f"Message content cannot be empty. Message index: {idx}, role: {msg['role']}")

        logger.info(f"Validation successful: Structured message list, count: {len(user_input.messages)}")
        return user_input.messages

    async def classify_message_type(
            self,
            message: str,
            config_id: UUID,
            db: Session,
            workspace_id: Optional[UUID] = None
    ) -> Dict:
        """
        Determine the type of user message (read or write)
        Updated to eliminate global variables in favor of explicit parameters.

        Args:
            message: User message to classify
            config_id: Configuration ID to load LLM model from database
            db: Database session
            workspace_id: Workspace ID for fallback lookup (optional)

        Returns:
            Type classification result
        """
        logger.info("Classifying message type")

        # Load configuration to get LLM model ID
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(
            config_id=config_id,
            workspace_id=workspace_id,
            service_name="MemoryAgentService"
        )

        status = await status_typle(message, memory_config.llm_model_id)
        logger.debug(f"Message type: {status}")
        return status

    async def generate_summary_from_retrieve(
            self,
            end_user_id: str,
            retrieve_info: str,
            history: List[Dict],
            query: str,
            config_id: str,
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
        # Always get workspace_id from end_user for fallback, even if config_id is provided
        workspace_id = None
        try:
            connected_config = get_end_user_connected_config(end_user_id, db)
            workspace_id = connected_config.get('workspace_id')
            if config_id is None:
                config_id = connected_config.get('memory_config_id')
            logger.info(f"Resolved config from end_user: config_id={config_id}, workspace_id={workspace_id}")
            if config_id is None and workspace_id is None:
                raise ValueError(
                    f"No memory configuration found for end_user {end_user_id}. Please ensure the user has a connected memory configuration.")
        except Exception as e:
            if "No memory configuration found" in str(e):
                raise  # Re-raise our specific error
            logger.error(f"Failed to get connected config for end_user {end_user_id}: {e}")
            if config_id is None:
                raise ValueError(f"Unable to determine memory configuration for end_user {end_user_id}: {e}")
            # If config_id was provided, continue without workspace_id fallback

        logger.info(f"Generating summary from retrieve info for query: {query[:50]}...")

        try:
            # 加载配置
            config_service = MemoryConfigService(db)
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                workspace_id=workspace_id,
                service_name="MemoryAgentService"
            )

            # 导入必要的模块
            from app.core.memory.agent.langgraph_graph.nodes.summary_nodes import (
                summary_llm,
            )
            from app.core.memory.agent.models.summary_models import (
                RetrieveSummaryResponse,
            )

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
                template_name='direct_summary_prompt.jinja2',
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
            db: Session,
            end_user_id: Optional[str] = None,
            only_active: bool = True,
            current_workspace_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        统计知识库类型分布，包含：
        1. PostgreSQL 中的知识库类型：General, Web, Third-party, Folder（根据 workspace_id 过滤）
        2. total: 所有类型的总和

        参数：
        - end_user_id: 用户组ID（可选，保留参数以保持接口兼容性）
        - only_active: 是否仅统计有效记录
        - current_workspace_id: 当前工作空间ID（可选，未提供时知识库统计为 0）
        - db: 数据库会话

        返回格式：
        {
            "General": count,
            "Web": count,
            "Third-party": count,
            "Folder": count,
            "total": sum_of_all
        }
        """
        result = {}

        # 1. 统计 PostgreSQL 中的知识库类型
        try:
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

        # 2. 统计 Neo4j 中的 memory 总量已移除
        # memory 字段不再返回

        # 3. 计算知识库类型总和（不包括 memory）
        result["total"] = (
                result.get("General", 0) +
                result.get("Web", 0) +
                result.get("Third-party", 0) +
                result.get("Folder", 0)
        )

        return result

    async def get_interest_distribution_by_user(
            self,
            end_user_id: Optional[str] = None,
            limit: int = 5,
            language: str = "zh"
    ) -> List[Dict[str, Any]]:
        """
        获取指定用户的兴趣分布标签。
        
        与热门标签不同，此接口专注于识别用户的兴趣活动（运动、爱好、学习等），
        过滤掉纯物品、工具、地点等不代表用户主动参与活动的名词。

        参数：
        - end_user_id: 用户ID（必填）
        - limit: 返回标签数量限制
        - language: 输出语言（"zh" 中文, "en" 英文）

        返回格式：
        [
            {"name": "兴趣活动名", "frequency": 频次},
            ...
        ]
        """
        try:
            tags = await get_interest_distribution(end_user_id, limit=limit, by_user=False, language=language)
            return [{"name": tag, "frequency": freq} for tag, freq in tags]
        except Exception as e:
            logger.error(f"兴趣分布标签查询失败: {e}")
            raise Exception(f"兴趣分布标签查询失败: {e}")

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
                    tags: list[str] = Field(...,
                                            description="3个描述用户特征的标签，如：产品设计师、旅行爱好者、摄影发烧友")

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
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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


# TODO: move to memory_config_service.py
def get_end_user_connected_config(end_user_id: str, db: Session) -> Dict[str, Any]:
    """
    获取终端用户关联的记忆配置

    兼容旧数据：如果 end_user.memory_config_id 为空，则从 AppRelease.config 中获取
    并回填到 end_user.memory_config_id 字段（懒迁移）。

    Args:
        end_user_id: 终端用户ID
        db: 数据库会话

    Returns:
        包含 memory_config_id, workspace_id 和相关信息的字典

    Raises:
        ValueError: 当终端用户不存在或应用未发布时
    """
    import json as json_module

    from sqlalchemy import select

    from app.models.app_model import App
    from app.models.app_release_model import AppRelease
    from app.models.end_user_model import EndUser
    from app.services.memory_config_service import MemoryConfigService

    logger.info(f"Getting connected config for end_user: {end_user_id}")

    # TODO: check sources for enduserid, should be one of these three: chat, draft, apikey
    # 1. 获取 end_user 及其 app_id
    end_user = db.query(EndUser).filter(EndUser.id == end_user_id).first()
    if not end_user:
        logger.warning(f"End user not found: {end_user_id}")
        raise ValueError(f"终端用户不存在: {end_user_id}")

    app_id = end_user.app_id
    logger.debug(f"Found end_user app_id: {app_id}")

    # 2. 获取应用以确定 workspace_id
    app = db.query(App).filter(App.id == app_id).first()
    if not app:
        logger.warning(f"App not found: {app_id}")
        # raise ValueError(f"应用不存在: {app_id}")
    # TODO: temp fix for draft run
    # if not app.current_release_id:
    #     logger.warning(f"No current release for app: {app_id}")
    #     raise ValueError(f"应用未发布: {app_id}")

    # 3. 兼容旧数据：如果 memory_config_id 为空，从 AppRelease.config 获取并回填
    memory_config_id_to_use = end_user.memory_config_id

    # 如果已有 memory_config_id，直接使用
    # 如果新创建enduser，enduser.memory_config_id 必定为none
    # 那么使用从release中获取memory_config_id为预期行为，并且回填到
    # end_user.memory_config_id
    if not memory_config_id_to_use:
        logger.info(f"end_user.memory_config_id is None, migrating from AppRelease.config")

        # 获取最新发布版本
        stmt = (
            select(AppRelease)
            .where(AppRelease.app_id == app_id, AppRelease.is_active.is_(True))
            .order_by(AppRelease.version.desc())
        )
        # TODO: change to current_release_id
        latest_release = db.scalars(stmt).first()

        if latest_release:
            config = latest_release.config or {}

            # 如果 config 是字符串，解析为字典
            if isinstance(config, str):
                try:
                    config = json_module.loads(config)
                except json_module.JSONDecodeError:
                    logger.warning(f"Failed to parse config JSON for release {latest_release.id}")
                    config = {}

            # 使用 MemoryConfigService 的提取方法
            memory_config_service = MemoryConfigService(db)
            legacy_config_id, is_legacy_int = memory_config_service.extract_memory_config_id(
                app_type=app.type,
                config=config
            )

            if legacy_config_id:
                # 验证提取的 config_id 是否存在于数据库中
                from app.models.memory_config_model import (
                    MemoryConfig as MemoryConfigModel,
                )
                existing_config = db.get(MemoryConfigModel, legacy_config_id)

                if existing_config:
                    memory_config_id_to_use = legacy_config_id

                    # 回填到 end_user 表（lazy update）
                    end_user.memory_config_id = memory_config_id_to_use
                    db.commit()
                    logger.info(
                        f"Migrated memory_config_id for end_user {end_user_id}: {memory_config_id_to_use}"
                    )
                else:
                    logger.warning(
                        f"Extracted memory_config_id does not exist, skipping backfill: "
                        f"end_user_id={end_user_id}, config_id={legacy_config_id}"
                    )
            elif is_legacy_int:
                logger.info(
                    f"Legacy int config detected for end_user {end_user_id}, will use workspace default"
                )

    # 4. 使用 get_config_with_fallback 获取记忆配置
    memory_config_service = MemoryConfigService(db)
    memory_config = memory_config_service.get_config_with_fallback(
        memory_config_id=memory_config_id_to_use,
        workspace_id=end_user.workspace_id
    )

    memory_config_id = str(memory_config.config_id) if memory_config else None

    result = {
        "end_user_id": str(end_user_id),
        "memory_config_id": memory_config_id,
        "workspace_id": str(end_user.workspace_id)
    }

    logger.info(
        f"Successfully retrieved connected config: memory_config_id={memory_config_id}, workspace_id={app.workspace_id}")
    return result


def get_end_users_connected_configs_batch(end_user_ids: List[str], db: Session) -> Dict[str, Dict[str, Any]]:
    """
    批量获取多个终端用户关联的记忆配置（优化版本，减少数据库查询次数）

    使用与 get_end_user_connected_config 相同的逻辑：
    1. 优先使用 end_user.memory_config_id
    2. 如果没有，尝试从 AppRelease.config 提取并回填
    3. 如果仍然没有，回退到工作空间默认配置

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
    import json as json_module

    from sqlalchemy import select

    from app.models.app_model import App
    from app.models.app_release_model import AppRelease
    from app.models.end_user_model import EndUser
    from app.models.memory_config_model import MemoryConfig
    from app.services.memory_config_service import MemoryConfigService

    logger.info(f"Batch getting connected configs for {len(end_user_ids)} end_users")

    result = {}

    if not end_user_ids:
        return result

    # 1. 批量查询所有 end_user 及其 app_id 和 memory_config_id
    end_users = db.query(EndUser).filter(EndUser.id.in_(end_user_ids)).all()

    # 创建映射 - 保留 EndUser 对象引用以便回填
    end_user_map = {str(eu.id): eu for eu in end_users}
    user_data = {str(eu.id): {"app_id": eu.app_id, "memory_config_id": eu.memory_config_id} for eu in end_users}

    # 记录未找到的用户
    found_user_ids = set(user_data.keys())
    missing_user_ids = set(end_user_ids) - found_user_ids
    if missing_user_ids:
        logger.warning(f"End users not found: {missing_user_ids}")
        for user_id in missing_user_ids:
            result[user_id] = {"memory_config_id": None, "memory_config_name": None}

    # 2. 批量获取所有相关应用以获取 workspace_id 和 type
    app_ids = list(set(data["app_id"] for data in user_data.values()))
    if not app_ids:
        return result

    apps = db.query(App).filter(App.id.in_(app_ids)).all()
    app_map = {app.id: app for app in apps}
    app_to_workspace = {app.id: app.workspace_id for app in apps}

    # 3. 对于没有 memory_config_id 的用户，尝试从 AppRelease.config 提取
    users_needing_migration = [
        (end_user_id, data["app_id"])
        for end_user_id, data in user_data.items()
        if not data["memory_config_id"]
    ]

    if users_needing_migration:
        # 批量获取相关应用的最新发布版本
        migration_app_ids = list(set(app_id for _, app_id in users_needing_migration))

        # 查询每个应用的最新活跃发布版本
        app_latest_releases = {}
        for app_id in migration_app_ids:
            stmt = (
                select(AppRelease)
                .where(AppRelease.app_id == app_id, AppRelease.is_active.is_(True))
                .order_by(AppRelease.version.desc())
                .limit(1)
            )
            latest_release = db.scalars(stmt).first()
            if latest_release:
                app_latest_releases[app_id] = latest_release

        # 为每个需要迁移的用户提取 memory_config_id
        config_service = MemoryConfigService(db)
        users_to_backfill = []  # [(end_user, memory_config_id), ...]

        for end_user_id, app_id in users_needing_migration:
            latest_release = app_latest_releases.get(app_id)
            if not latest_release:
                continue

            config = latest_release.config or {}

            # 如果 config 是字符串，解析为字典
            if isinstance(config, str):
                try:
                    config = json_module.loads(config)
                except json_module.JSONDecodeError:
                    logger.warning(f"Failed to parse config JSON for release {latest_release.id}")
                    continue

            # 使用 MemoryConfigService 的提取方法
            app = app_map.get(app_id)
            if not app:
                continue

            legacy_config_id, is_legacy_int = config_service.extract_memory_config_id(
                app_type=app.type,
                config=config
            )

            if legacy_config_id:
                # 更新 user_data 中的 memory_config_id
                user_data[end_user_id]["memory_config_id"] = legacy_config_id

                # 记录需要回填的用户（稍后验证配置存在后再回填）
                end_user = end_user_map.get(end_user_id)
                if end_user:
                    users_to_backfill.append((end_user, legacy_config_id))
            elif is_legacy_int:
                logger.info(
                    f"Legacy int config detected for end_user {end_user_id}, will use workspace default"
                )

        # 验证提取的 config_id 是否存在于数据库中
        if users_to_backfill:
            config_ids_to_validate = list(set(cid for _, cid in users_to_backfill))
            existing_configs = db.query(MemoryConfig).filter(
                MemoryConfig.config_id.in_(config_ids_to_validate)
            ).all()
            valid_config_ids = {mc.config_id for mc in existing_configs}

            # 只回填存在的配置
            valid_backfills = [
                (eu, cid) for eu, cid in users_to_backfill
                if cid in valid_config_ids
            ]
            invalid_backfills = [
                (eu, cid) for eu, cid in users_to_backfill
                if cid not in valid_config_ids
            ]

            if invalid_backfills:
                invalid_ids = [str(cid) for _, cid in invalid_backfills]
                logger.warning(
                    f"Skipping backfill for non-existent memory_config_ids: {invalid_ids}"
                )
                # 清除 user_data 中无效的 config_id
                for eu, cid in invalid_backfills:
                    user_data[str(eu.id)]["memory_config_id"] = None

            # 批量回填 end_user.memory_config_id
            if valid_backfills:
                for end_user, memory_config_id in valid_backfills:
                    end_user.memory_config_id = memory_config_id
                db.commit()
                logger.info(f"Migrated memory_config_id for {len(valid_backfills)} end_users")

    # 4. 收集需要查询的 memory_config_id 和需要回退的 workspace_id
    direct_config_ids = []
    workspace_fallback_users = []  # [(end_user_id, workspace_id), ...]

    for end_user_id, data in user_data.items():
        if data["memory_config_id"]:
            direct_config_ids.append(data["memory_config_id"])
        else:
            workspace_id = app_to_workspace.get(data["app_id"])
            if workspace_id:
                workspace_fallback_users.append((end_user_id, workspace_id))

    # 5. 批量查询直接分配的配置
    config_id_to_config = {}
    if direct_config_ids:
        configs = db.query(MemoryConfig).filter(MemoryConfig.config_id.in_(direct_config_ids)).all()
        config_id_to_config = {mc.config_id: mc for mc in configs}

    # 6. 获取工作空间默认配置（需要逐个查询，因为 get_workspace_default_config 有复杂逻辑）
    workspace_default_configs = {}
    unique_workspace_ids = list(set(ws_id for _, ws_id in workspace_fallback_users))

    if unique_workspace_ids:
        config_service = MemoryConfigService(db)
        for workspace_id in unique_workspace_ids:
            default_config = config_service.get_workspace_default_config(workspace_id)
            if default_config:
                workspace_default_configs[workspace_id] = default_config

    # 7. 构建最终结果
    for end_user_id, data in user_data.items():
        memory_config = None

        # 优先使用 end_user 直接分配的配置
        if data["memory_config_id"]:
            memory_config = config_id_to_config.get(data["memory_config_id"])

        # 回退到工作空间默认配置
        if not memory_config:
            workspace_id = app_to_workspace.get(data["app_id"])
            if workspace_id:
                memory_config = workspace_default_configs.get(workspace_id)

        if memory_config:
            result[end_user_id] = {
                "memory_config_id": str(memory_config.config_id),
                "memory_config_name": memory_config.config_name
            }
        else:
            result[end_user_id] = {"memory_config_id": None, "memory_config_name": None}

    logger.info(f"Successfully retrieved {len(result)} connected configs")
    return result
