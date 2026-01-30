"""
LangChain Agent 封装

使用 LangChain 1.x 标准方式
- 使用 create_agent 创建 agent graph
- 支持工具调用循环
- 支持流式输出
- 使用 RedBearLLM 支持多提供商
"""
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from app.core.memory.agent.langgraph_graph.tools.write_tool import agent_chat_messages, format_parsing, messages_parse
from app.core.memory.agent.langgraph_graph.write_graph import long_term_storage
from app.db import get_db
from app.core.logging_config import get_business_logger
from app.core.memory.agent.utils.redis_tool import store
from app.core.models import RedBearLLM, RedBearModelConfig
from app.models.models_model import ModelType
from app.repositories.memory_short_repository import LongTermMemoryRepository
from app.services.memory_agent_service import (
    get_end_user_connected_config,
)
from app.services.memory_konwledges_server import write_rag
from app.services.task_service import get_task_memory_write_result
from app.tasks import write_message_task
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.utils.config_utils import resolve_config_id

logger = get_business_logger()


class LangChainAgent:

    def __init__(
        self,
        model_name: str,
        api_key: str,
        provider: str = "openai",
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        system_prompt: Optional[str] = None,
        tools: Optional[Sequence[BaseTool]] = None,
        streaming: bool = False
    ):
        """初始化 LangChain Agent

        Args:
            model_name: 模型名称
            api_key: API Key
            provider: 提供商（openai, xinference, gpustack, ollama, dashscope）
            api_base: API 基础 URL
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 系统提示词
            tools: 工具列表（可选，框架自动走 ReAct 循环）
            streaming: 是否启用流式输出（默认 True）
        """
        self.model_name = model_name
        self.provider = provider
        self.system_prompt = system_prompt or "你是一个专业的AI助手"
        self.tools = tools or []
        self.streaming = streaming

        # 创建 RedBearLLM（支持多提供商）
        model_config = RedBearModelConfig(
            model_name=model_name,
            provider=provider,
            api_key=api_key,
            base_url=api_base,
            extra_params={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "streaming": streaming  # 使用参数控制流式
            }
        )

        self.llm = RedBearLLM(model_config, type=ModelType.CHAT)

        # 获取底层模型用于真正的流式调用
        self._underlying_llm = self.llm._model if hasattr(self.llm, '_model') else self.llm

        # 确保底层模型也启用流式
        if streaming and hasattr(self._underlying_llm, 'streaming'):
            self._underlying_llm.streaming = True

        # 使用 create_agent 创建 agent graph（LangChain 1.x 标准方式）
        # 无论是否有工具，都使用 agent 统一处理
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools if self.tools else None,
            system_prompt=self.system_prompt
        )

        logger.info(
            "LangChain Agent 初始化完成",
            extra={
                "model": model_name,
                "provider": provider,
                "has_api_base": bool(api_base),
                "temperature": temperature,
                "streaming": streaming,
                "tool_count": len(self.tools),
                "tool_names": [tool.name for tool in self.tools] if self.tools else [],
                "tool_count": len(self.tools)
            }
        )

    def _prepare_messages(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[str] = None
    ) -> List[BaseMessage]:
        """准备消息列表

        Args:
            message: 用户消息
            history: 历史消息列表
            context: 上下文信息

        Returns:
            List[BaseMessage]: 消息列表
        """
        messages = []

        # 添加系统提示词
        messages.append(SystemMessage(content=self.system_prompt))

        # 添加历史消息
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        # 添加当前用户消息
        user_content = message
        if context:
            user_content = f"参考信息：\n{context}\n\n用户问题：\n{user_content}"

        messages.append(HumanMessage(content=user_content))
        return messages

    async def term_memory_save(self,long_term_messages,actual_config_id,end_user_id,type):
        db = next(get_db())
        scope=6
        try:
            repo = LongTermMemoryRepository(db)
            await long_term_storage(long_term_type="chunk", langchain_messages=long_term_messages,
                                    memory_config=actual_config_id, end_user_id=end_user_id, scope=scope)

            from app.core.memory.agent.utils.redis_tool import write_store
            result = write_store.get_session_by_userid(end_user_id)
            if type=="chunk" or type=="aggregate":
                data = await format_parsing(result, "dict")
                chunk_data = data[:scope]
                if len(chunk_data)==scope:
                    repo.upsert(end_user_id, chunk_data)
                    logger.info(f'写入短长期：')
            else:
                long_time_data = write_store.find_user_recent_sessions(end_user_id, 5)
                long_messages = await messages_parse(long_time_data)
                repo.upsert(end_user_id, long_messages)
                logger.info(f'写入短长期：')
        finally:
            db.close()

    async def write(self, storage_type, end_user_id, user_message, ai_message, user_rag_memory_id, actual_end_user_id, actual_config_id):
        """
        写入记忆（支持结构化消息）

        Args:
            storage_type: 存储类型 (neo4j/rag)
            end_user_id: 终端用户ID
            user_message: 用户消息内容
            ai_message: AI 回复内容
            user_rag_memory_id: RAG 记忆ID
            actual_end_user_id: 实际用户ID
            actual_config_id: 配置ID

        逻辑说明：
        - RAG 模式：组合 user_message 和 ai_message 为字符串格式，保持原有逻辑不变
        - Neo4j 模式：使用结构化消息列表
          1. 如果 user_message 和 ai_message 都不为空：创建配对消息 [user, assistant]
          2. 如果只有 user_message：创建单条用户消息 [user]（用于历史记忆场景）
          3. 每条消息会被转换为独立的 Chunk，保留 speaker 字段
        """

        db = next(get_db())
        try:
            actual_config_id=resolve_config_id(actual_config_id, db)

            if storage_type == "rag":
                # RAG 模式：组合消息为字符串格式（保持原有逻辑）
                combined_message = f"user: {user_message}\nassistant: {ai_message}"
                await write_rag(end_user_id, combined_message, user_rag_memory_id)
                logger.info(f'RAG_Agent:{end_user_id};{user_rag_memory_id}')
            else:
                # Neo4j 模式：使用结构化消息列表
                structured_messages = []

                # 始终添加用户消息（如果不为空）
                if user_message:
                    structured_messages.append({"role": "user", "content": user_message})

                # 只有当 AI 回复不为空时才添加 assistant 消息
                if ai_message:
                    structured_messages.append({"role": "assistant", "content": ai_message})

                # 如果没有消息，直接返回
                if not structured_messages:
                    logger.warning(f"No messages to write for user {actual_end_user_id}")
                    return

                logger.info(f"[WRITE] Submitting Celery task - user={actual_end_user_id}, messages={len(structured_messages)}, config={actual_config_id}")
                write_id = write_message_task.delay(
                    actual_end_user_id,  # end_user_id: 用户ID
                    structured_messages,  # message: 结构化消息列表 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
                    actual_config_id,    # config_id: 配置ID
                    storage_type,        # storage_type: "neo4j"
                    user_rag_memory_id   # user_rag_memory_id: RAG记忆ID（Neo4j模式下不使用）
                )
                logger.info(f"[WRITE] Celery task submitted - task_id={write_id}")
                write_status = get_task_memory_write_result(str(write_id))
                logger.info(f'[WRITE] Task result - user={actual_end_user_id}, status={write_status}')
        finally:
            db.close()
    async def chat(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            end_user_id: Optional[str] = None,
            config_id: Optional[str] = None,  # 添加这个参数
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            memory_flag: Optional[bool] = True
    ) -> Dict[str, Any]:
        """执行对话

        Args:
            message: 用户消息
            history: 历史消息列表 [{"role": "user/assistant", "content": "..."}]
            context: 上下文信息（如知识库检索结果）

        Returns:
            Dict: 包含 content 和元数据的字典
        """
        message_chat= message
        start_time = time.time()
        actual_config_id = config_id
        # If config_id is None, try to get from end_user's connected config
        if actual_config_id is None and end_user_id:
            try:
                from app.services.memory_agent_service import (
                    get_end_user_connected_config,
                )
                db = next(get_db())
                try:
                    connected_config = get_end_user_connected_config(end_user_id, db)
                    actual_config_id = connected_config.get("memory_config_id")
                except Exception as e:
                    logger.warning(f"Failed to get connected config for end_user {end_user_id}: {e}")
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to get db session: {e}")
        actual_end_user_id = end_user_id if end_user_id is not None else "unknown"
        logger.info(f'写入类型{storage_type,str(end_user_id), message, str(user_rag_memory_id)}')
        print(f'写入类型{storage_type,str(end_user_id), message, str(user_rag_memory_id)}')
        try:
            # 准备消息列表
            messages = self._prepare_messages(message, history, context)

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "has_context": bool(context),
                    "has_history": bool(history),
                    "has_tools": bool(self.tools),
                    "message_count": len(messages)
                }
            )

            # 统一使用 agent.invoke 调用
            result = await self.agent.ainvoke({"messages": messages})

            # 获取最后的 AI 消息
            output_messages = result.get("messages", [])
            content = ""
            for msg in reversed(output_messages):
                if isinstance(msg, AIMessage):
                    content = msg.content
                    break

            elapsed_time = time.time() - start_time
            if memory_flag:
                long_term_messages=await agent_chat_messages(message_chat,content)
                # AI 回复写入（用户消息和 AI 回复配对，一次性写入完整对话）
                await self.write(storage_type, actual_end_user_id, message_chat, content, user_rag_memory_id, actual_end_user_id, actual_config_id)
                '''长期'''
                await self.term_memory_save(long_term_messages,actual_config_id,end_user_id,"chunk")
            response = {
                "content": content,
                "model": self.model_name,
                "elapsed_time": elapsed_time,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }

            logger.debug(
                "Agent 调用完成",
                extra={
                    "elapsed_time": elapsed_time,
                    "content_length": len(response["content"])
                }
            )

            return response

        except Exception as e:
            logger.error("Agent 调用失败", extra={"error": str(e)})
            raise

    async def chat_stream(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[str] = None,
        end_user_id:Optional[str] = None,
        config_id: Optional[str] = None,
        storage_type:Optional[str] = None,
        user_rag_memory_id:Optional[str] = None,
        memory_flag: Optional[bool] = True
    ) -> AsyncGenerator[str, None]:
        """执行流式对话

        Args:
            message: 用户消息
            history: 历史消息列表
            context: 上下文信息

        Yields:
            str: 消息内容块
        """
        logger.info("=" * 80)
        logger.info(" chat_stream 方法开始执行")
        logger.info(f"  Message: {message[:100]}")
        logger.info(f"  Has tools: {bool(self.tools)}")
        logger.info(f"  Tool count: {len(self.tools) if self.tools else 0}")
        logger.info("=" * 80)
        message_chat = message
        actual_config_id = config_id
        # If config_id is None, try to get from end_user's connected config
        if actual_config_id is None and end_user_id:
            try:
                db = next(get_db())
                try:
                    connected_config = get_end_user_connected_config(end_user_id, db)
                    actual_config_id = connected_config.get("memory_config_id")
                except Exception as e:
                    logger.warning(f"Failed to get connected config for end_user {end_user_id}: {e}")
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to get db session: {e}")


            # 注意：不在这里写入用户消息，等 AI 回复后一起写入
        try:
            # 准备消息列表
            messages = self._prepare_messages(message, history, context)

            logger.debug(
                f"准备流式调用，has_tools={bool(self.tools)}, message_count={len(messages)}"
            )

            chunk_count = 0
            yielded_content = False

            # 统一使用 agent 的 astream_events 实现流式输出
            logger.debug("使用 Agent astream_events 实现流式输出")
            full_content=''
            try:
                async for event in self.agent.astream_events(
                    {"messages": messages},
                    version="v2"
                ):
                    chunk_count += 1
                    kind = event.get("event")
                    
                    # 处理所有可能的流式事件
                    if kind == "on_chat_model_stream":
                        # LLM 流式输出
                        chunk = event.get("data", {}).get("chunk")
                        full_content+=chunk.content
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            yield chunk.content
                            yielded_content = True
                    
                    elif kind == "on_llm_stream":
                        # 另一种 LLM 流式事件
                        chunk = event.get("data", {}).get("chunk")
                        if chunk:
                            if hasattr(chunk, "content") and chunk.content:
                                full_content+=chunk.content
                                yield chunk.content
                                yielded_content = True
                            elif isinstance(chunk, str):
                                yield chunk
                                yielded_content = True
                    
                    # 记录工具调用（可选）
                    elif kind == "on_tool_start":
                        logger.debug(f"工具调用开始: {event.get('name')}")
                    elif kind == "on_tool_end":
                        logger.debug(f"工具调用结束: {event.get('name')}")
                
                logger.debug(f"Agent 流式完成，共 {chunk_count} 个事件")
                if memory_flag:
                    # AI 回复写入（用户消息和 AI 回复配对，一次性写入完整对话）
                    long_term_messages = await agent_chat_messages(message_chat, full_content)
                    await self.write(storage_type, end_user_id, message_chat, full_content, user_rag_memory_id, end_user_id, actual_config_id)
                    await self.term_memory_save(long_term_messages, actual_config_id, end_user_id, "chunk")
                
            except Exception as e:
                logger.error(f"Agent astream_events 失败: {str(e)}", exc_info=True)
                raise

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"chat_stream 异常: {str(e)}")
            logger.error("=" * 80, exc_info=True)
            raise
        finally:
            logger.info("=" * 80)
            logger.info("chat_stream 方法执行结束")
            logger.info("=" * 80)


