"""基于分享链接的聊天服务"""
import asyncio
import json
import time
import uuid
from typing import Optional, Dict, Any, AsyncGenerator, Annotated, List

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.agent.langchain_agent import LangChainAgent
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.models import MultiAgentConfig, AgentConfig, ModelType
from app.models import WorkflowConfig
from app.repositories.tool_repository import ToolRepository
from app.schemas import DraftRunRequest
from app.schemas.app_schema import FileInput
from app.schemas.model_schema import ModelInfo
from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
from app.services.conversation_service import ConversationService
from app.services.draft_run_service import AgentRunService
from app.services.model_service import ModelApiKeyService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.services.multimodal_service import MultimodalService
from app.services.workflow_service import WorkflowService
from app.schemas import FileType

logger = get_business_logger()


class AppChatService:
    """基于分享链接的聊天服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_service = ConversationService(db)
        self.agent_service = AgentRunService(db)
        self.workflow_service = WorkflowService(db)

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
            workspace_id: Optional[str] = None,
            files: Optional[List[FileInput]] = None
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        start_time = time.time()
        config_id = None

        # 应用 features 配置
        features_config: dict = config.features or {}
        if hasattr(features_config, 'model_dump'):
            features_config = features_config.model_dump()
        web_search_feature = features_config.get("web_search", {})
        if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
            web_search = False

        # 校验文件上传
        self.agent_service._validate_file_upload(features_config, files)

        variables = self.agent_service.prepare_variables(variables, config.variables)

        # 获取模型配置ID
        model_config_id = config.default_model_config_id
        api_key_obj = ModelApiKeyService.get_available_api_key(self.db, model_config_id)
        # 处理系统提示词（支持变量替换）
        system_prompt = config.system_prompt
        if variables:
            system_prompt_rendered = render_prompt_message(
                system_prompt,
                PromptMessageRole.USER,
                variables
            )
            system_prompt = system_prompt_rendered.get_text_content() or system_prompt

        # 准备工具列表
        tools = []

        # 获取工具服务
        tenant_id = ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

        tools.extend(self.agent_service.load_tools_config(config.tools, web_search, tenant_id))
        skill_tools, skill_prompts = self.agent_service.load_skill_config(config.skills, message, tenant_id)
        tools.extend(skill_tools)
        if skill_prompts:
            system_prompt = f"{system_prompt}\n\n{skill_prompts}"
        tools.extend(self.agent_service.load_knowledge_retrieval_config(config.knowledge_retrieval, user_id))
        memory_flag = False
        if memory:
            memory_tools, memory_flag = self.agent_service.load_memory_config(
                config.memory, user_id, storage_type, user_rag_memory_id
            )
            tools.extend(memory_tools)

        # 获取模型参数
        model_parameters = config.model_parameters

        # 创建 LangChain Agent
        agent = LangChainAgent(
            model_name=api_key_obj.model_name,
            api_key=api_key_obj.api_key,
            provider=api_key_obj.provider,
            api_base=api_key_obj.api_base,
            is_omni=api_key_obj.is_omni,
            temperature=model_parameters.get("temperature", 0.7),
            max_tokens=model_parameters.get("max_tokens", 2000),
            system_prompt=system_prompt,
            tools=tools,

        )

        model_info = ModelInfo(
            model_name=api_key_obj.model_name,
            provider=api_key_obj.provider,
            api_key=api_key_obj.api_key,
            api_base=api_key_obj.api_base,
            capability=api_key_obj.capability,
            is_omni=api_key_obj.is_omni,
            model_type=ModelType.LLM
        )

        # 加载历史消息
        messages = self.conversation_service.get_messages(
            conversation_id=conversation_id,
            limit=10
        )
        history = []
        for msg in messages:
            content = [{"type": "text", "text": msg.content}]

            # 处理 meta_data 中的 files
            if msg.meta_data and msg.meta_data.get("files"):
                files = msg.meta_data.get("files", [])
                # 使用 MultimodalService 处理文件
                multimodal_service = MultimodalService(self.db, api_config=model_info)

                # 将 files 转换为 FileInput 格式
                file_inputs = []
                for file in files:
                    from app.schemas.app_schema import FileInput, TransferMethod
                    file_input = FileInput(
                        type=file.get("type"),
                        transfer_method=TransferMethod.REMOTE_URL,
                        url=file.get("url")
                    )
                    file_inputs.append(file_input)

                history_processed_files = await multimodal_service.history_process_files(files=file_inputs)

                content.extend(history_processed_files)

            history.append({
                "role": msg.role,
                "content": content
            })

        # 处理多模态文件
        processed_files = None
        if files:
            multimodal_service = MultimodalService(self.db, model_info)
            processed_files = await multimodal_service.process_files(user_id, files)
            logger.info(f"处理了 {len(processed_files)} 个文件")

        # 调用 Agent（支持多模态）
        result = await agent.chat(
            message=message,
            history=history,
            context=None,
            end_user_id=user_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            config_id=config_id,
            memory_flag=memory_flag,
            files=processed_files  # 传递处理后的文件
        )

        ModelApiKeyService.record_api_key_usage(self.db, api_key_obj.id)

        elapsed_time = time.time() - start_time

        # suggested_questions
        suggested_questions = []
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if isinstance(sq_config, dict) and sq_config.get("enabled"):
            suggested_questions = await self.agent_service._generate_suggested_questions(
                features_config, result["content"],
                {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
                 "api_base": api_key_obj.api_base}, {}
            )

        audio_url = await self.agent_service._generate_tts(
            features_config, result["content"],
            {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
             "api_base": api_key_obj.api_base, "provider": api_key_obj.provider},
            tenant_id=tenant_id, workspace_id=workspace_id
        )

        # 构建用户消息内容（含多模态文件）
        human_meta = {
            "files": []
        }
        assistant_meta = {
            "model": api_key_obj.model_name,
            "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
            "audio_url": None
        }
        if files:
            for f in files:
                # url = await MultimodalService(self.db).get_file_url(f)
                human_meta["files"].append({
                    "type": f.type,
                    "url": f.url
                })

        # 保存消息
        if audio_url:
            assistant_meta["audio_url"] = audio_url
        self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="user",
            content=message,
            meta_data=human_meta
        )
        ai_message = self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=result["content"],
            meta_data=assistant_meta
        )
        message_id = ai_message.id

        return {
            "conversation_id": conversation_id,
            "message_id": str(message_id),
            "message": result["content"],
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time,
            "suggested_questions": suggested_questions,
            "citations": self.agent_service._filter_citations(features_config, result.get("citations", [])),
            "audio_url": audio_url,
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
            workspace_id: Optional[str] = None,
            files: Optional[List[FileInput]] = None
    ) -> AsyncGenerator[str, None]:
        """聊天（流式）"""

        try:
            start_time = time.time()
            config_id = None
            message_id = uuid.uuid4()

            # 应用 features 配置
            features_config: dict = config.features or {}
            if hasattr(features_config, 'model_dump'):
                features_config = features_config.model_dump()
            web_search_feature = features_config.get("web_search", {})
            if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
                web_search = False

            # 校验文件上传
            self.agent_service._validate_file_upload(features_config, files)

            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id)}, ensure_ascii=False)}\n\n"

            variables = self.agent_service.prepare_variables(variables, config.variables)
            # 获取模型配置ID
            model_config_id = config.default_model_config_id
            api_key_obj = ModelApiKeyService.get_available_api_key(self.db, model_config_id)
            # 处理系统提示词（支持变量替换）
            system_prompt = config.system_prompt
            if variables:
                system_prompt_rendered = render_prompt_message(
                    system_prompt,
                    PromptMessageRole.USER,
                    variables
                )
                system_prompt = system_prompt_rendered.get_text_content() or system_prompt

            # 准备工具列表
            tools = []

            # 获取工具服务
            tenant_id = ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

            tools.extend(self.agent_service.load_tools_config(config.tools, web_search, tenant_id))

            skill_tools, skill_prompts = self.agent_service.load_skill_config(config.skills, message, tenant_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            tools.extend(self.agent_service.load_knowledge_retrieval_config(config.knowledge_retrieval, user_id))
            # 添加长期记忆工具
            memory_flag = False
            if memory:
                memory_tools, memory_flag = self.agent_service.load_memory_config(
                    config.memory, user_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 获取模型参数
            model_parameters = config.model_parameters

            # 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_obj.model_name,
                api_key=api_key_obj.api_key,
                provider=api_key_obj.provider,
                api_base=api_key_obj.api_base,
                is_omni=api_key_obj.is_omni,
                temperature=model_parameters.get("temperature", 0.7),
                max_tokens=model_parameters.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                streaming=True
            )

            model_info = ModelInfo(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                api_base=api_key_obj.api_base,
                capability=api_key_obj.capability,
                is_omni=api_key_obj.is_omni,
                model_type=ModelType.LLM
            )

            # 加载历史消息
            messages = self.conversation_service.get_messages(
                conversation_id=conversation_id,
                limit=10
            )
            history = []
            for msg in messages:
                content = [{"type": "text", "text": msg.content}]

                # 处理 meta_data 中的 files
                if msg.meta_data and msg.meta_data.get("files"):
                    files = msg.meta_data.get("files", [])
                    # 使用 MultimodalService 处理文件
                    multimodal_service = MultimodalService(self.db, api_config=model_info)

                    # 将 files 转换为 FileInput 格式
                    file_inputs = []
                    for file in files:
                        from app.schemas.app_schema import FileInput, TransferMethod
                        file_input = FileInput(
                            type=file.get("type"),
                            transfer_method=TransferMethod.REMOTE_URL,
                            url=file.get("url")
                        )
                        file_inputs.append(file_input)

                    history_processed_files = await multimodal_service.history_process_files(files=file_inputs)

                    content.extend(history_processed_files)

                history.append({
                    "role": msg.role,
                    "content": content
                })

            # 处理多模态文件
            processed_files = None
            if files:
                multimodal_service = MultimodalService(self.db, model_info)
                processed_files = await multimodal_service.process_files(user_id, files)
                logger.info(f"处理了 {len(processed_files)} 个文件")

            # 流式调用 Agent（支持多模态），同时并行启动 TTS
            full_content = ""
            total_tokens = 0

            text_queue: asyncio.Queue = asyncio.Queue()
            api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "api_base": api_key_obj.api_base,
                "provider": api_key_obj.provider,
            }
            stream_audio_url, tts_task = await self.agent_service._generate_tts_streaming(
                features_config, api_key_config,
                text_queue=text_queue,
                tenant_id=tenant_id, workspace_id=workspace_id
            )

            async for chunk in agent.chat_stream(
                    message=message,
                    history=history,
                    context=None,
                    end_user_id=user_id,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                    config_id=config_id,
                    memory_flag=memory_flag,
                    files=processed_files
            ):
                if isinstance(chunk, int):
                    total_tokens = chunk
                else:
                    full_content += chunk
                    yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                    if tts_task is not None:
                        await text_queue.put(chunk)

            if tts_task is not None:
                await text_queue.put(None)

            elapsed_time = time.time() - start_time
            ModelApiKeyService.record_api_key_usage(self.db, api_key_obj.id)

            # 发送结束事件（包含 suggested_questions、tts、citations）
            end_data: dict = {"elapsed_time": elapsed_time, "message_length": len(full_content), "error": None}
            sq_config = features_config.get("suggested_questions_after_answer", {})
            if isinstance(sq_config, dict) and sq_config.get("enabled"):
                end_data["suggested_questions"] = await self.agent_service._generate_suggested_questions(
                    features_config, full_content,
                    {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
                     "api_base": api_key_obj.api_base}, {}
                )
            end_data["audio_url"] = stream_audio_url
            end_data["citations"] = self.agent_service._filter_citations(features_config, [])

            # 保存消息
            human_meta = {
                "files":[]
            }
            assistant_meta = {
                "model": api_key_obj.model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                "audio_url": None
            }

            if files:
                for f in files:
                    # url = await MultimodalService(self.db).get_file_url(f)
                    human_meta["files"].append({
                        "type": f.type,
                        "url": f.url
                    })

            if stream_audio_url:
                assistant_meta["audio_url"] = stream_audio_url
            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data=human_meta
            )
            self.conversation_service.add_message(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                meta_data=assistant_meta
            )
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
            yield f"event: end\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

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

        ai_message = self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=result.get("message", ""),
            meta_data={
                "mode": result.get("mode"),
                "elapsed_time": result.get("elapsed_time"),
                "usage": result.get("usage", {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                })
            }
        )

        return {
            "conversation_id": conversation_id,
            "message": result.get("message", ""),
            "message_id": str(ai_message.id),
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

        if variables is None:
            variables = {}

        try:
            message_id = uuid.uuid4()
            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id)}, ensure_ascii=False)}\n\n"

            full_content = ""
            total_tokens = 0

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
                if "sub_usage" in event:
                    if "data:" in event:
                        try:
                            data_line = event.split("data: ", 1)[1].strip()
                            data = json.loads(data_line)
                            if "total_tokens" in data:
                                total_tokens += data["total_tokens"]
                        except:
                            pass
                else:
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
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                meta_data={
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": total_tokens
                    }
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

    async def workflow_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            files: Optional[List[FileInput]] = None,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id),
            stream=True,
            user_id=user_id,
            files=files
        )
        return await self.workflow_service.run(
            app_id=app_id,
            payload=payload,
            config=config,
            workspace_id=workspace_id,
            release_id=release_id,
        )

    async def workflow_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: str = None,
            variables: Optional[Dict[str, Any]] = None,
            files: Optional[List[FileInput]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            public=False

    ) -> AsyncGenerator[dict, None]:
        """聊天（流式）"""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id),
            stream=True,
            user_id=user_id,
            files=files
        )
        async for event in self.workflow_service.run_stream(
                app_id=app_id,
                payload=payload,
                config=config,
                workspace_id=workspace_id,
                release_id=release_id,
                public=public
        ):
            yield event


# ==================== 依赖注入函数 ====================

def get_app_chat_service(
        db: Annotated[Session, Depends(get_db)]
) -> AppChatService:
    """获取工作流服务（依赖注入）"""
    return AppChatService(db)
