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
        streaming: bool = False,
        max_iterations: Optional[int] = None,  # 最大迭代次数（None 表示自动计算）
        max_tool_consecutive_calls: int = 3  # 单个工具最大连续调用次数
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
            streaming: 是否启用流式输出
            max_iterations: 最大迭代次数（None 表示自动计算：基础 5 次 + 每个工具 2 次）
            max_tool_consecutive_calls: 单个工具最大连续调用次数（默认 3 次）
        """
        self.model_name = model_name
        self.provider = provider
        self.tools = tools or []
        self.streaming = streaming
        self.max_tool_consecutive_calls = max_tool_consecutive_calls
        
        # 工具调用计数器：记录每个工具的连续调用次数
        self.tool_call_counter: Dict[str, int] = {}
        self.last_tool_called: Optional[str] = None
        
        # 根据工具数量动态调整最大迭代次数
        # 基础值 + 每个工具额外的调用机会
        if max_iterations is None:
            # 自动计算：基础 5 次 + 每个工具 2 次额外机会
            self.max_iterations = 5 + len(self.tools) * 2
        else:
            self.max_iterations = max_iterations
        
        self.system_prompt = system_prompt or "你是一个专业的AI助手"
        
        logger.debug(
            f"Agent 迭代次数配置: max_iterations={self.max_iterations}, "
            f"tool_count={len(self.tools)}, "
            f"max_tool_consecutive_calls={self.max_tool_consecutive_calls}, "
            f"auto_calculated={max_iterations is None}"
        )

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

        # 包装工具以跟踪连续调用次数
        wrapped_tools = self._wrap_tools_with_tracking(self.tools) if self.tools else None

        # 使用 create_agent 创建 agent graph（LangChain 1.x 标准方式）
        # 无论是否有工具，都使用 agent 统一处理
        self.agent = create_agent(
            model=self.llm,
            tools=wrapped_tools,
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
                "max_iterations": self.max_iterations,
                "max_tool_consecutive_calls": self.max_tool_consecutive_calls,
                "tool_count": len(self.tools),
                "tool_names": [tool.name for tool in self.tools] if self.tools else [],
                # "tool_count": len(self.tools)
            }
        )

    def _wrap_tools_with_tracking(self, tools: Sequence[BaseTool]) -> List[BaseTool]:
        """包装工具以跟踪连续调用次数
        
        Args:
            tools: 原始工具列表
            
        Returns:
            List[BaseTool]: 包装后的工具列表
        """
        from langchain_core.tools import StructuredTool
        from functools import wraps
        
        wrapped_tools = []
        
        for original_tool in tools:
            tool_name = original_tool.name
            original_func = original_tool.func if hasattr(original_tool, 'func') else None
            
            if not original_func:
                # 如果无法获取原始函数，直接使用原工具
                wrapped_tools.append(original_tool)
                continue
            
            # 创建包装函数
            def make_wrapped_func(tool_name, original_func):
                """创建包装函数的工厂函数，避免闭包问题"""
                @wraps(original_func)
                def wrapped_func(*args, **kwargs):
                    """包装后的工具函数，跟踪连续调用次数"""
                    # 检查是否是连续调用同一个工具
                    if self.last_tool_called == tool_name:
                        self.tool_call_counter[tool_name] = self.tool_call_counter.get(tool_name, 0) + 1
                    else:
                        # 切换到新工具，重置计数器
                        self.tool_call_counter[tool_name] = 1
                        self.last_tool_called = tool_name
                    
                    current_count = self.tool_call_counter[tool_name]
                    
                    logger.debug(
                        f"工具调用: {tool_name}, 连续调用次数: {current_count}/{self.max_tool_consecutive_calls}"
                    )
                    
                    # 检查是否超过最大连续调用次数
                    if current_count > self.max_tool_consecutive_calls:
                        logger.warning(
                            f"工具 '{tool_name}' 连续调用次数已达上限 ({self.max_tool_consecutive_calls})，"
                            f"返回提示信息"
                        )
                        return (
                            f"工具 '{tool_name}' 已连续调用 {self.max_tool_consecutive_calls} 次，"
                            f"未找到有效结果。请尝试其他方法或直接回答用户的问题。"
                        )
                    
                    # 调用原始工具函数
                    return original_func(*args, **kwargs)
                
                return wrapped_func
            
            # 使用 StructuredTool 创建新工具
            wrapped_tool = StructuredTool(
                name=original_tool.name,
                description=original_tool.description,
                func=make_wrapped_func(tool_name, original_func),
                args_schema=original_tool.args_schema if hasattr(original_tool, 'args_schema') else None
            )
            
            wrapped_tools.append(wrapped_tool)
        
        return wrapped_tools

    def _prepare_messages(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[str] = None,
        files: Optional[List[Dict[str, Any]]] = None
    ) -> List[BaseMessage]:
        """准备消息列表

        Args:
            message: 用户消息
            history: 历史消息列表
            context: 上下文信息
            files: 多模态文件内容列表（已处理）

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

        # 构建用户消息（支持多模态）
        if files and len(files) > 0:
            content_parts = self._build_multimodal_content(user_content, files)
            messages.append(HumanMessage(content=content_parts))
        else:
            # 纯文本消息
            messages.append(HumanMessage(content=user_content))

        return messages
    
    def _build_multimodal_content(self, text: str, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        构建多模态消息内容
        
        Args:
            text: 文本内容
            files: 文件列表（已由 MultimodalService 处理为对应 provider 的格式）
            
        Returns:
            List[Dict]: 消息内容列表
        """
        # 根据 provider 使用不同的文本格式
        if self.provider.lower() in ["bedrock", "anthropic"]:
            # Anthropic/Bedrock: {"type": "text", "text": "..."}
            content_parts = [{"type": "text", "text": text}]
        else:
            # 通义千问等: {"text": "..."}
            content_parts = [{"text": text}]
        
        # 添加文件内容
        # MultimodalService 已经根据 provider 返回了正确格式，直接使用
        content_parts.extend(files)
        
        logger.debug(
            f"构建多模态消息: provider={self.provider}, "
            f"parts={len(content_parts)}, "
            f"files={len(files)}"
        )
        
        return content_parts

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
            memory_flag: Optional[bool] = True,
            files: Optional[List[Dict[str, Any]]] = None  # 新增：多模态文件
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
            # 准备消息列表（支持多模态）
            messages = self._prepare_messages(message, history, context, files)

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "has_context": bool(context),
                    "has_history": bool(history),
                    "has_tools": bool(self.tools),
                    "has_files": bool(files),
                    "message_count": len(messages),
                    "max_iterations": self.max_iterations
                }
            )

            # 统一使用 agent.invoke 调用
            # 通过 recursion_limit 限制最大迭代次数，防止工具调用死循环
            try:
                result = await self.agent.ainvoke(
                    {"messages": messages},
                    config={"recursion_limit": self.max_iterations}
                )
            except RecursionError as e:
                logger.warning(
                    f"Agent 达到最大迭代次数限制 ({self.max_iterations})，可能存在工具调用循环",
                    extra={"error": str(e)}
                )
                # 返回一个友好的错误提示
                return {
                    "content": f"抱歉，我在处理您的请求时遇到了问题。已达到最大处理步骤限制（{self.max_iterations}次）。请尝试简化您的问题或稍后再试。",
                    "model": self.model_name,
                    "elapsed_time": time.time() - start_time,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0
                    }
                }

            # 获取最后的 AI 消息
            output_messages = result.get("messages", [])
            content = ""
            
            logger.debug(f"输出消息数量: {len(output_messages)}")
            total_tokens = 0
            for msg in reversed(output_messages):
                if isinstance(msg, AIMessage):
                    logger.debug(f"找到 AI 消息，content 类型: {type(msg.content)}")
                    logger.debug(f"AI 消息内容: {msg.content}")
                    
                    # 处理多模态响应：content 可能是字符串或列表
                    if isinstance(msg.content, str):
                        content = msg.content
                        logger.debug(f"提取字符串内容，长度: {len(content)}")
                    elif isinstance(msg.content, list):
                        # 多模态响应：提取文本部分
                        logger.debug(f"多模态响应，列表长度: {len(msg.content)}")
                        text_parts = []
                        for item in msg.content:
                            logger.debug(f"处理项: {item}")
                            if isinstance(item, dict):
                                # 通义千问格式: {"text": "..."}
                                if "text" in item:
                                    text = item.get("text", "")
                                    text_parts.append(text)
                                    logger.debug(f"提取文本: {text[:100]}...")
                                # OpenAI 格式: {"type": "text", "text": "..."}
                                elif item.get("type") == "text":
                                    text = item.get("text", "")
                                    text_parts.append(text)
                                    logger.debug(f"提取文本: {text[:100]}...")
                            elif isinstance(item, str):
                                text_parts.append(item)
                                logger.debug(f"提取字符串: {item[:100]}...")
                        content = "".join(text_parts)
                        logger.debug(f"合并后内容长度: {len(content)}")
                    else:
                        content = str(msg.content)
                        logger.debug(f"转换为字符串: {content[:100]}...")
                    response_meta = msg.response_metadata if hasattr(msg, 'response_metadata') else None
                    total_tokens = response_meta.get("token_usage", {}).get("total_tokens", 0) if response_meta else 0
                    break
            
            logger.info(f"最终提取的内容长度: {len(content)}")

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
                    "total_tokens": total_tokens
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
        memory_flag: Optional[bool] = True,
        files: Optional[List[Dict[str, Any]]] = None  # 新增：多模态文件
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
            # 准备消息列表（支持多模态）
            messages = self._prepare_messages(message, history, context, files)

            logger.debug(
                f"准备流式调用，has_tools={bool(self.tools)}, has_files={bool(files)}, message_count={len(messages)}"
            )

            chunk_count = 0
            yielded_content = False

            # 统一使用 agent 的 astream_events 实现流式输出
            logger.debug("使用 Agent astream_events 实现流式输出")
            full_content = ''
            try:
                async for event in self.agent.astream_events(
                    {"messages": messages},
                    version="v2",
                    config={"recursion_limit": self.max_iterations}
                ):
                    chunk_count += 1
                    kind = event.get("event")
                    
                    # 处理所有可能的流式事件
                    if kind == "on_chat_model_stream":
                        # LLM 流式输出
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content"):
                            # 处理多模态响应：content 可能是字符串或列表
                            chunk_content = chunk.content
                            if isinstance(chunk_content, str) and chunk_content:
                                full_content += chunk_content
                                yield chunk_content
                                yielded_content = True
                            elif isinstance(chunk_content, list):
                                # 多模态响应：提取文本部分
                                for item in chunk_content:
                                    if isinstance(item, dict):
                                        # 通义千问格式: {"text": "..."}
                                        if "text" in item:
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                yield text
                                                yielded_content = True
                                        # OpenAI 格式: {"type": "text", "text": "..."}
                                        elif item.get("type") == "text":
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                yield text
                                                yielded_content = True
                                    elif isinstance(item, str):
                                        full_content += item
                                        yield item
                                        yielded_content = True
                    
                    elif kind == "on_llm_stream":
                        # 另一种 LLM 流式事件
                        chunk = event.get("data", {}).get("chunk")
                        if chunk:
                            if hasattr(chunk, "content"):
                                chunk_content = chunk.content
                                if isinstance(chunk_content, str) and chunk_content:
                                    full_content += chunk_content
                                    yield chunk_content
                                    yielded_content = True
                                elif isinstance(chunk_content, list):
                                    # 多模态响应：提取文本部分
                                    for item in chunk_content:
                                        if isinstance(item, dict):
                                            # 通义千问格式: {"text": "..."}
                                            if "text" in item:
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    yield text
                                                    yielded_content = True
                                            # OpenAI 格式: {"type": "text", "text": "..."}
                                            elif item.get("type") == "text":
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    yield text
                                                    yielded_content = True
                                        elif isinstance(item, str):
                                            full_content += item
                                            yield item
                                            yielded_content = True
                            elif isinstance(chunk, str):
                                full_content += chunk
                                yield chunk
                                yielded_content = True
                    
                    # 记录工具调用（可选）
                    elif kind == "on_tool_start":
                        logger.debug(f"工具调用开始: {event.get('name')}")
                    elif kind == "on_tool_end":
                        logger.debug(f"工具调用结束: {event.get('name')}")
                
                logger.debug(f"Agent 流式完成，共 {chunk_count} 个事件")
                # 统计token消耗
                output_messages = event.get("data", {}).get("output", {}).get("messages", [])
                for msg in reversed(output_messages):
                    if isinstance(msg, AIMessage):
                        response_meta = msg.response_metadata if hasattr(msg, 'response_metadata') else None
                        total_tokens = response_meta.get("token_usage", {}).get("total_tokens",
                                                                                0) if response_meta else 0
                        yield total_tokens
                        break
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


