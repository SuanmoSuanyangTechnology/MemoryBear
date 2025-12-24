"""基于分享链接的聊天服务"""
import asyncio
import json
import time
import uuid
from typing import Optional, Dict, Any, AsyncGenerator, Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.agent.langchain_agent import LangChainAgent
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.models import MultiAgentConfig, AgentConfig
from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
from app.services.conversation_service import ConversationService
from app.services.draft_run_service import create_knowledge_retrieval_tool, create_long_term_memory_tool
from app.services.draft_run_service import create_web_search_tool
from app.services.model_service import ModelApiKeyService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator

logger = get_business_logger()


class AppChatService:
    """基于分享链接的聊天服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_service = ConversationService(db)

    async def agnet_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """聊天（非流式）"""

        start_time = time.time()
        config_id = None

        if variables is None:
            variables = {}

        # 获取模型配置ID
        model_config_id = config.default_model_config_id
        api_key_obj = ModelApiKeyService.get_a_api_key(model_config_id)
        # 处理系统提示词（支持变量替换）
        system_prompt = config.get("system_prompt", "")
        if variables:
            system_prompt_rendered = render_prompt_message(
                system_prompt,
                PromptMessageRole.USER,
                variables
            )
            system_prompt = system_prompt_rendered.get_text_content() or system_prompt

        # 准备工具列表
        tools = []

        # 添加知识库检索工具
        knowledge_retrieval = config.get("knowledge_retrieval")
        if knowledge_retrieval:
            knowledge_bases = knowledge_retrieval.get("knowledge_bases", [])
            kb_ids = [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
            if kb_ids:
                kb_tool = create_knowledge_retrieval_tool(knowledge_retrieval, kb_ids, user_id)
                tools.append(kb_tool)

        # 添加长期记忆工具
        memory_flag = False
        if memory == True:
            memory_config = config.get("memory", {})
            if memory_config.get("enabled") and user_id:
                memory_flag = True
                memory_tool = create_long_term_memory_tool(memory_config, user_id)
                tools.append(memory_tool)

        web_tools = config.get("tools")
        web_search_choice = web_tools.get("web_search", {})
        web_search_enable = web_search_choice.get("enabled", False)
        if web_search == True:
            if web_search_enable == True:
                search_tool = create_web_search_tool({})
                tools.append(search_tool)

                logger.debug(
                    "已添加网络搜索工具",
                    extra={
                        "tool_count": len(tools)
                    }
                )

        # 获取模型参数
        model_parameters = config.get("model_parameters", {})

        # 创建 LangChain Agent
        agent = LangChainAgent(
            model_name=api_key_obj.model_name,
            api_key=api_key_obj.api_key,
            provider=api_key_obj.provider,
            api_base=api_key_obj.api_base,
            temperature=model_parameters.get("temperature", 0.7),
            max_tokens=model_parameters.get("max_tokens", 2000),
            system_prompt=system_prompt,
            tools=tools,

        )

        # 加载历史消息
        history = []
        memory_config = {"enabled": True, 'max_history': 10}
        if memory_config.get("enabled"):
            messages = self.conversation_service.get_messages(
                conversation_id=conversation_id,
                limit=memory_config.get("max_history", 10)
            )
            history = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]

        # 调用 Agent
        result = await agent.chat(
            message=message,
            history=history,
            context=None,
            end_user_id=user_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            config_id=config_id,
            memory_flag=memory_flag
        )

        # 保存消息
        self.conversation_service.save_conversation_messages(
            conversation_id=conversation_id,
            user_message=message,
            assistant_message=result["content"]
        )

        elapsed_time = time.time() - start_time

        return {
            "conversation_id": conversation_id,
            "message": result["content"],
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time
        }

    async def agnet_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """聊天（流式）"""

        try:
            start_time = time.time()
            config_id = None

            if variables is None:
                variables = {}

            # 获取模型配置ID
            model_config_id = config.default_model_config_id
            api_key_obj = ModelApiKeyService.get_a_api_key(model_config_id)
            # 处理系统提示词（支持变量替换）
            system_prompt = config.get("system_prompt", "")
            if variables:
                system_prompt_rendered = render_prompt_message(
                    system_prompt,
                    PromptMessageRole.USER,
                    variables
                )
                system_prompt = system_prompt_rendered.get_text_content() or system_prompt

            # 准备工具列表
            tools = []

            # 添加知识库检索工具
            knowledge_retrieval = config.get("knowledge_retrieval")
            if knowledge_retrieval:
                knowledge_bases = knowledge_retrieval.get("knowledge_bases", [])
                kb_ids = [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
                if kb_ids:
                    kb_tool = create_knowledge_retrieval_tool(knowledge_retrieval, kb_ids, user_id)
                    tools.append(kb_tool)

            # 添加长期记忆工具
            memory_flag = False
            if memory:
                memory_config = config.get("memory", {})
                if memory_config.get("enabled") and user_id:
                    memory_flag = True
                    memory_tool = create_long_term_memory_tool(memory_config, user_id)
                    tools.append(memory_tool)

            web_tools = config.get("tools")
            web_search_choice = web_tools.get("web_search", {})
            web_search_enable = web_search_choice.get("enabled", False)
            if web_search == True:
                if web_search_enable == True:
                    search_tool = create_web_search_tool({})
                    tools.append(search_tool)

                    logger.debug(
                        "已添加网络搜索工具",
                        extra={
                            "tool_count": len(tools)
                        }
                    )

            # 获取模型参数
            model_parameters = config.get("model_parameters", {})

            # 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_obj.model_name,
                api_key=api_key_obj.api_key,
                provider=api_key_obj.provider,
                api_base=api_key_obj.api_base,
                temperature=model_parameters.get("temperature", 0.7),
                max_tokens=model_parameters.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                streaming=True
            )

            # 加载历史消息
            history = []
            memory_config = {"enabled": True, 'max_history': 10}
            if memory_config.get("enabled"):
                messages = self.conversation_service.get_messages(
                    conversation_id=conversation_id,
                    limit=memory_config.get("max_history", 10)
                )
                history = [
                    {"role": msg.role, "content": msg.content}
                    for msg in messages
                ]

            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"

            # 流式调用 Agent
            full_content = ""
            async for chunk in agent.chat_stream(
                    message=message,
                    history=history,
                    context=None,
                    end_user_id=user_id,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                    config_id=config_id,
                    memory_flag=memory_flag
            ):
                full_content += chunk
                # 发送消息块事件
                yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

            elapsed_time = time.time() - start_time

            # 保存消息
            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message
            )

            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                meta_data={
                    "model": api_key_obj.model_name,
                    "usage": {}
                }
            )

            # 发送结束事件
            end_data = {"elapsed_time": elapsed_time, "message_length": len(full_content)}
            yield f"event: end\ndata: {json.dumps(end_data, ensure_ascii=False)}\n\n"

            logger.info(
                "流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except (GeneratorExit, asyncio.CancelledError):
            # 生成器被关闭或任务被取消，正常退出
            logger.debug("流式聊天被中断")
            raise
        except Exception as e:
            logger.error(f"流式聊天失败: {str(e)}", exc_info=True)
            # 发送错误事件
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    async def multi_agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """多 Agent 聊天（非流式）"""

        start_time = time.time()
        actual_config_id = None
        config_id = actual_config_id

        if variables is None:
            variables = {}

        # 2. 创建编排器
        orchestrator = MultiAgentOrchestrator(self.db, config)

        # 3. 执行任务
        result = await orchestrator.execute(
            message=message,
            conversation_id=conversation_id,
            user_id=user_id,
            variables=variables,
            use_llm_routing=True,  # 默认启用 LLM 路由
            web_search=web_search,  # 网络搜索参数
            memory=memory  # 记忆功能参数
        )

        elapsed_time = time.time() - start_time

        # 保存消息
        self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="user",
            content=message
        )

        self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=result.get("message", ""),
            meta_data={
                "mode": result.get("mode"),
                "elapsed_time": result.get("elapsed_time"),
                "sub_results": result.get("sub_results")
            }
        )

        return {
            "conversation_id": conversation_id,
            "message": result.get("message", ""),
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "elapsed_time": elapsed_time
        }

    async def multi_agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """多 Agent 聊天（流式）"""

        start_time = time.time()
        actual_config_id = None
        config_id = actual_config_id

        if variables is None:
            variables = {}

        try:

            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"

            full_content = ""

            # 2. 创建编排器
            orchestrator = MultiAgentOrchestrator(self.db, config)

            # 3. 流式执行任务
            async for event in orchestrator.execute_stream(
                    message=message,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    variables=variables,
                    use_llm_routing=True,
                    web_search=web_search,  # 网络搜索参数
                    memory=memory,  # 记忆功能参数
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id
            ):
                yield event
                # 尝试提取内容（用于保存）
                if "data:" in event:
                    try:
                        data_line = event.split("data: ", 1)[1].strip()
                        data = json.loads(data_line)
                        if "content" in data:
                            full_content += data["content"]
                    except:
                        pass

            elapsed_time = time.time() - start_time

            # 保存消息
            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message
            )

            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                meta_data={
                    "elapsed_time": elapsed_time
                }
            )

            logger.info(
                "多 Agent 流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )


        except (GeneratorExit, asyncio.CancelledError):
            # 生成器被关闭或任务被取消，正常退出
            logger.debug("多 Agent 流式聊天被中断")
            raise
        except Exception as e:
            logger.error(f"多 Agent 流式聊天失败: {str(e)}", exc_info=True)
            # 发送错误事件
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"


# ==================== 依赖注入函数 ====================

def get_app_chat_service(
        db: Annotated[Session, Depends(get_db)]
) -> ChatService:
    """获取工作流服务（依赖注入）"""
    return ChatService(db)
