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
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.db import get_db
from app.models import (
    MultiAgentConfig, AgentConfig, ModelType, WorkflowConfig,
    ModelCapability, AgentExecution, Message, Conversation)
from app.repositories.agent_execution_repository import AgentExecutionRepository
from app.repositories.tool_repository import ToolRepository
from app.schemas import DraftRunRequest
from app.schemas.app_schema import FileInput, FileType, TransferMethod
from app.schemas.model_schema import ModelInfo
from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
from app.services.conversation_service import ConversationService
from app.services.draft_run_service import AgentRunService
from app.services.model_service import ModelApiKeyService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.services.multimodal_service import MultimodalService
from app.services.workflow_service import WorkflowService
from app.models.file_metadata_model import FileMetadata
from app.services.tool_orchestrator import ToolOrchestrator

logger = get_business_logger()


class AppChatService:
    """基于分享链接的聊天服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_service = ConversationService(db)
        self.agent_service = AgentRunService(db)
        self.workflow_service = WorkflowService(db)

    def _check_annotation_match(self, app_id: uuid.UUID, message: str, source: str = "") -> Optional[dict]:
        """检查是否命中标注

        Args:
            app_id: 应用ID
            message: 用户消息
            source: 来源（用于记录命中来源）

        Returns:
            命中返回标注结果字典，未命中返回None
        """
        try:
            from app.services.annotation_service import AnnotationService
            service = AnnotationService(self.db)
            setting = service.get_setting(app_id)
            if not setting or not setting.enabled:
                return None
            if not setting.model_config_id:
                return None

            annotations = service.repo.get_all_active_by_app(app_id)
            if not annotations:
                return None

            from app.models.models_model import ModelConfig
            model_cfg = self.db.query(ModelConfig).filter(
                ModelConfig.id == setting.model_config_id
            ).first()
            if not model_cfg:
                return None

            api_key_obj = ModelApiKeyService.get_available_api_key(self.db, setting.model_config_id)
            if not api_key_obj:
                return None

            from app.core.models.base import RedBearModelConfig
            model_config = RedBearModelConfig(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                base_url=api_key_obj.api_base or None,
                timeout=60,
                max_retries=3,
            )

            result = service.find_best_match(
                query=message,
                annotations=annotations,
                threshold=setting.similarity_threshold,
                model_config=model_config,
                app_id=app_id,
                source=source,
            )
            return result
        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    async def agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        start_time = time.time()
        message_id = uuid.uuid4()

        # 检查标注命中
        from app.models.annotation_model import HitLogSource
        annotation_match = self._check_annotation_match(
            config.app_id,
            message,
            source=source or HitLogSource.EXTERNAL
        )
        if annotation_match:
            message_id = uuid.uuid4()
            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data={"files": []}
            )
            ai_message = self.conversation_service.add_message(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=annotation_match["answer"],
                meta_data={"usage": {}}
            )
            elapsed_time = time.time() - start_time
            return {
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "message": annotation_match["answer"],
                "reasoning_content": None,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "elapsed_time": elapsed_time,
                "suggested_questions": [],
                "citations": [],
                "audio_url": None,
                "audio_status": None
            }

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
        kb_tools, citations_collector = self.agent_service.load_knowledge_retrieval_config(config.knowledge_retrieval,
                                                                                           user_id)
        tools.extend(kb_tools)
        if memory:
            memory_tools, _ = self.agent_service.load_memory_config(
                config.memory, user_id, storage_type, user_rag_memory_id
            )
            tools.extend(memory_tools)

        # 获取模型参数
        model_parameters = config.model_parameters

        model_info = ModelInfo(
            model_name=api_key_obj.model_name,
            provider=api_key_obj.provider,
            api_key=api_key_obj.api_key,
            api_base=api_key_obj.api_base,
            capability=api_key_obj.capability,
            is_omni=api_key_obj.is_omni,
            model_type=ModelType.LLM
        )

        # 加载历史消息（包含开场白）
        if history is None:
            # 没有外部传入的历史，从数据库加载
            history = await self.conversation_service.get_conversation_history(
                conversation_id=conversation_id,
                max_history=10,
                current_provider=api_key_obj.provider,
                current_is_omni=api_key_obj.is_omni
            )

        # 如果是新会话且有开场白，作为第一条 assistant 消息写入数据库
        is_new_conversation = len(history) == 0
        if is_new_conversation:
            opening, suggested_questions = self.agent_service._get_opening_statement(features_config, True, variables)
            if opening:
                self.conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=opening,
                    meta_data={"suggested_questions": suggested_questions}
                )
                # 重新加载历史（包含刚写入的开场白）
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=10,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni
                )

        # 处理多模态文件
        processed_files = None
        if files:
            multimodal_service = MultimodalService(self.db, model_info)
            fu_config = features_config.get("file_upload", {})
            if hasattr(fu_config, "model_dump"):
                fu_config = fu_config.model_dump()
            doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
            processed_files = await multimodal_service.process_files(
                files, document_image_recognition=doc_img_recognition,
                workspace_id=workspace_id
            )
            logger.info(f"处理了 {len(processed_files)} 个文件")
            if doc_img_recognition and ModelCapability.VISION in (api_key_obj.capability or []) and any(
                f.type == FileType.DOCUMENT for f in files
            ):
                system_prompt += (
                    "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                    "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                    "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                    "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                )

        # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
        capability = api_key_obj.capability or []
        orchestrator_node_executions = []
        _api_key_config = {
            "model_name": api_key_obj.model_name,
            "api_key": api_key_obj.api_key,
            "provider": api_key_obj.provider,
            "api_base": api_key_obj.api_base,
            "is_omni": api_key_obj.is_omni,
            "capability": capability,
        }
        if ModelCapability.FUNCTION_CALL not in capability and tools:
            system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                tools=tools,
                system_prompt=system_prompt,
                message=message,
                history=history,
                api_key_config=_api_key_config,
                model_config=model_info,
                effective_params=model_parameters,
                processed_files=processed_files,
            )
            tools = []

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
            deep_thinking=model_parameters.get("deep_thinking", False),
            thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
            json_output=model_parameters.get("json_output", False),
            capability=capability,
        )

        # 为需要运行时上下文的工具注入上下文
        for t in tools:
            if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                t.tool_instance.set_runtime_context(
                    user_id=user_id or "anonymous",
                    conversation_id=str(conversation_id) if conversation_id else None,
                    uploaded_files=processed_files or []
                )

        # 创建 Agent 执行记录（pending 状态，对齐工作流行为）
        import datetime as dt
        from app.models.app_model import App
        agent_exec_repo = AgentExecutionRepository(self.db)
        app_obj = self.db.get(App, config.app_id)
        agent_execution = AgentExecution(
            app_id=config.app_id,
            conversation_id=conversation_id,
            message_id=None,
            agent_config_id=config.id,
            release_id=app_obj.current_release_id if app_obj else None,
            triggered_by=None,
            steps=[],
            status="running",
            started_at=dt.datetime.fromtimestamp(start_time),
            meta_data={
                "model": api_key_obj.model_name,
                "provider": api_key_obj.provider,
            },
        )
        agent_exec_repo.create(agent_execution)
        self.db.commit()

        try:
            # 调用 Agent（支持多模态）
            result = await agent.chat(
                message=message,
                history=history,
                context=None,
                files=processed_files
            )
        except Exception as e:
            # Agent 执行失败，更新记录为 failed
            elapsed_time = time.time() - start_time
            agent_exec_repo.update_completed(
                execution_id=agent_execution.id,
                steps=[],
                status="failed",
                elapsed_time=elapsed_time,
                error_message=str(e)[:2000],
            )
            raise

        ModelApiKeyService.record_api_key_usage(self.db, api_key_obj.id)

        elapsed_time = time.time() - start_time

        # suggested_questions
        suggested_questions = []
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if isinstance(sq_config, dict) and sq_config.get("enabled"):
            suggested_questions = await self.agent_service._generate_suggested_questions(
                features_config, result["content"],
                _api_key_config, {}
            )

        audio_url = await self.agent_service._generate_tts(
            features_config, result["content"],
            {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
             "api_base": api_key_obj.api_base, "provider": api_key_obj.provider},
            tenant_id=tenant_id, workspace_id=workspace_id
        )

        # 过滤 citations（只调用一次）
        filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)

        # 构建用户消息内容（含多模态文件）
        human_meta = {
            "files": [],
            "history_files": {}
        }
        assistant_meta = {
            "model": api_key_obj.model_name,
            "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
            "audio_url": None,
            "citations": filtered_citations,
            "suggested_questions": suggested_questions,
            "reasoning_content": result.get("reasoning_content")
        }
        if files:
            local_ids = [f.upload_file_id for f in files
                         if f.transfer_method.value == "local_file" and f.upload_file_id
                         and (not f.name or not f.size)]
            meta_map = {}
            if local_ids:
                rows = self.db.query(FileMetadata).filter(
                    FileMetadata.id.in_(local_ids),
                    FileMetadata.status == "completed"
                ).all()
                meta_map = {str(r.id): r for r in rows}
            for f in files:
                name, size = f.name, f.size
                if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                    meta = meta_map.get(str(f.upload_file_id))
                    if meta:
                        name = name or meta.file_name
                        size = size or meta.file_size
                human_meta["files"].append({
                    "type": f.type,
                    "url": f.url,
                    "name": name,
                    "size": size,
                    "file_type": f.file_type,
                })

        if processed_files:
            human_meta["history_files"] = {
                "content": processed_files,
                "provider": api_key_obj.provider,
                "is_omni": api_key_obj.is_omni
            }

        if audio_url:
            assistant_meta["audio_url"] = audio_url
        # 长期记忆写入由 conversation_service.add_message → MemoryService.sync_message
        # → SlidingWindowScheduler 统一接管，这里不再触发老的 write_long_term 路径。
        if not skip_save:
            self.conversation_service.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data=human_meta,
                should_memorize=memory,
            )
            self.conversation_service.add_message(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                meta_data=assistant_meta,
                should_memorize=memory,
            )
        else:
            new_msg = Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                version=version,
                is_current=True,
                parent_message_id=parent_message_id,
                meta_data=assistant_meta,
            )
            self.db.add(new_msg)
            conv = self.db.get(Conversation, conversation_id)
            if conv:
                conv.message_count += 1

            self.db.commit()

        # 更新 Agent 执行记录为 completed
        node_executions = orchestrator_node_executions + result.get("node_executions", [])
        agent_exec_repo.update_completed(
            execution_id=agent_execution.id,
            steps=node_executions,
            status="completed",
            elapsed_time=elapsed_time,
            token_usage=result.get("usage"),
            message_id=message_id,
        )

        return {
            "conversation_id": conversation_id,
            "message_id": str(message_id),
            "message": result["content"],
            "reasoning_content": result.get("reasoning_content"),
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time,
            "suggested_questions": suggested_questions,
            "citations": filtered_citations,
            "audio_url": audio_url,
            "audio_status": "pending" if audio_url else None
        }

    async def agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
    ) -> AsyncGenerator[str, None]:
        """聊天（流式）"""

        try:
            start_time = time.time()
            message_id = uuid.uuid4()

            # 检查标注命中
            from app.models.annotation_model import HitLogSource
            annotation_match = self._check_annotation_match(
                config.app_id,
                message,
                source=source or HitLogSource.EXTERNAL
            )
            if annotation_match:
                self.conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    meta_data={"files": []}
                )
                ai_message = self.conversation_service.add_message(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=annotation_match["answer"],
                    meta_data={"usage": {}}
                )
                yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id)}, ensure_ascii=False)}\n\n"
                yield f"event: message\ndata: {json.dumps({'content': annotation_match['answer'], 'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"
                yield f"event: end\ndata: {json.dumps({'elapsed_time': time.time() - start_time, 'message_length': len(annotation_match['answer']), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, ensure_ascii=False)}\n\n"
                return

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
            kb_tools, citations_collector = self.agent_service.load_knowledge_retrieval_config(
                config.knowledge_retrieval, user_id)
            tools.extend(kb_tools)
            # 添加长期记忆工具
            if memory:
                memory_tools, _ = self.agent_service.load_memory_config(
                    config.memory, user_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 获取模型参数
            model_parameters = config.model_parameters

            model_info = ModelInfo(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                api_base=api_key_obj.api_base,
                capability=api_key_obj.capability,
                is_omni=api_key_obj.is_omni,
                model_type=ModelType.LLM
            )

            # 加载历史消息（包含开场白）
            if history is None:
                # 没有外部传入的历史，从数据库加载
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=10,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni
                )

            # 如果是新会话且有开场白，作为第一条 assistant 消息写入数据库
            is_new_conversation = len(history) == 0
            if is_new_conversation:
                opening, suggested_questions = self.agent_service._get_opening_statement(features_config, True, variables)
                if opening:
                    self.conversation_service.add_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=opening,
                        meta_data={"suggested_questions": suggested_questions}
                    )
                    # 重新加载历史（包含刚写入的开场白）
                    history = await self.conversation_service.get_conversation_history(
                        conversation_id=conversation_id,
                        max_history=10,
                        current_provider=api_key_obj.provider,
                        current_is_omni=api_key_obj.is_omni
                    )

            # 处理多模态文件
            processed_files = None
            if files:
                multimodal_service = MultimodalService(self.db, model_info)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id
                )
                logger.info(f"处理了 {len(processed_files)} 个文件")
                if doc_img_recognition and ModelCapability.VISION in (api_key_obj.capability or []) and any(
                    f.type == FileType.DOCUMENT for f in files
                ):
                    from langchain.agents import create_agent
                    system_prompt += (
                        "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                        "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                        "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                        "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                    )

            # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
            capability = api_key_obj.capability or []
            orchestrator_node_executions = []
            _api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "provider": api_key_obj.provider,
                "api_base": api_key_obj.api_base,
                "is_omni": api_key_obj.is_omni,
                "capability": capability,
            }
            if ModelCapability.FUNCTION_CALL not in capability and tools:
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=message,
                    history=history,
                    api_key_config=_api_key_config,
                    model_config=model_info,
                    effective_params=model_parameters,
                    processed_files=processed_files,
                )
                # 把已完成的工具调用步骤作为事件补发给前端
                for step in orchestrator_node_executions:
                    event_type = "tool_error" if step.get("status") == "failed" else "tool_end"
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'input': step.get('input'), 'meta': step.get('meta')}, ensure_ascii=False)}\n\n"
                    yield f"event: {event_type}\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'output': step.get('output'), 'error': step.get('error'), 'meta': step.get('meta')}, ensure_ascii=False)}\n\n"
                tools = []

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
                streaming=True,
                deep_thinking=model_parameters.get("deep_thinking", False),
                thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
                json_output=model_parameters.get("json_output", False),
                capability=capability,
            )

            # 为需要运行时上下文的工具注入上下文
            for t in tools:
                if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                    t.tool_instance.set_runtime_context(
                        user_id=user_id or "anonymous",
                        conversation_id=str(conversation_id) if conversation_id else None,
                        uploaded_files=processed_files or []
                    )

            # 创建 Agent 执行记录（running 状态）
            import datetime as dt
            from app.models.app_model import App
            agent_exec_repo = AgentExecutionRepository(self.db)
            app_obj = self.db.get(App, config.app_id)
            agent_execution = AgentExecution(
                app_id=config.app_id,
                conversation_id=conversation_id,
                message_id=None,
                agent_config_id=config.id,
                release_id=app_obj.current_release_id if app_obj else None,
                triggered_by=None,
                steps=[],
                status="running",
                started_at=dt.datetime.fromtimestamp(start_time),
                meta_data={
                    "model": api_key_obj.model_name,
                    "provider": api_key_obj.provider,
                },
            )
            agent_exec_repo.create(agent_execution)
            self.db.commit()

            # 流式调用 Agent（支持多模态），同时并行启动 TTS
            full_content = ""
            full_reasoning = ""
            total_tokens = 0
            node_executions = []

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
                    files=processed_files
            ):
                if isinstance(chunk, int):
                    total_tokens = chunk
                elif isinstance(chunk, dict) and chunk.get("type") == "reasoning":
                    full_reasoning += chunk['content']
                    yield f"event: reasoning\ndata: {json.dumps({'content': chunk['content']}, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "node_executions":
                    node_executions = chunk.get("data", [])
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_start":
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'input': chunk.get('input'), 'meta': chunk.get('meta')}, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_end":
                    yield f"event: tool_end\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'output': chunk.get('output'), 'meta': chunk.get('meta')}, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_error":
                    yield f"event: tool_error\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'error': chunk.get('error')}, ensure_ascii=False)}\n\n"
                else:
                    full_content += chunk
                    yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                    if tts_task is not None:
                        await text_queue.put(chunk)

            if tts_task is not None:
                await text_queue.put(None)

            elapsed_time = time.time() - start_time
            ModelApiKeyService.record_api_key_usage(self.db, api_key_obj.id)

            # 发送结束事件（包含 suggested_questions、tts、audio_status、citations）
            end_data: dict = {"elapsed_time": elapsed_time, "message_length": len(full_content), "error": None}
            sq_config = features_config.get("suggested_questions_after_answer", {})
            suggested_questions = []
            if isinstance(sq_config, dict) and sq_config.get("enabled"):
                suggested_questions = await self.agent_service._generate_suggested_questions(
                    features_config, full_content,
                    _api_key_config, {}
                )
                end_data["suggested_questions"] = suggested_questions
            end_data["audio_url"] = stream_audio_url
            # 检查TTS是否已完成（非阻塞，不取消任务）
            audio_status = "pending"
            if tts_task is not None and tts_task.done():
                # 任务已完成，检查是否有异常
                try:
                    tts_task.result()
                    audio_status = "completed"
                except Exception as e:
                    logger.warning(f"TTS任务异常: {e}")
                    audio_status = "failed"
            end_data["audio_status"] = audio_status if stream_audio_url else None
            # 过滤 citations（只调用一次）
            filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)
            end_data["citations"] = filtered_citations

            # 保存消息
            human_meta = {
                "files": [],
                "history_files": {}
            }
            assistant_meta = {
                "model": api_key_obj.model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                "audio_url": None,
                "citations": filtered_citations,
                "suggested_questions": suggested_questions,
                "reasoning_content": full_reasoning or None
            }

            if files:
                local_ids = [f.upload_file_id for f in files
                             if f.transfer_method.value == "local_file" and f.upload_file_id
                             and (not f.name or not f.size)]
                meta_map = {}
                if local_ids:
                    rows = self.db.query(FileMetadata).filter(
                        FileMetadata.id.in_(local_ids),
                        FileMetadata.status == "completed"
                    ).all()
                    meta_map = {str(r.id): r for r in rows}
                for f in files:
                    name, size = f.name, f.size
                    if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                        meta = meta_map.get(str(f.upload_file_id))
                        if meta:
                            name = name or meta.file_name
                            size = size or meta.file_size
                    human_meta["files"].append({
                        "type": f.type,
                        "url": f.url,
                        "name": name,
                        "size": size,
                        "file_type": f.file_type,
                    })
            if processed_files:
                human_meta["history_files"] = {
                    "content": processed_files,
                    "provider": api_key_obj.provider,
                    "is_omni": api_key_obj.is_omni
                }

            if stream_audio_url:
                assistant_meta["audio_url"] = stream_audio_url

            # 长期记忆写入由 conversation_service.add_message → MemoryService.sync_message
            # → SlidingWindowScheduler 统一接管，这里不再触发老的 write_long_term 路径。
            if not skip_save:
                self.conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    meta_data=human_meta,
                    should_memorize=memory,
                )
                self.conversation_service.add_message(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    meta_data=assistant_meta,
                    should_memorize=memory,
                )
            else:
                new_msg = Message(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    version=version,
                    is_current=True,
                    parent_message_id=parent_message_id,
                    meta_data=assistant_meta,
                )
                self.db.add(new_msg)
                conv = self.db.get(Conversation, conversation_id)
                if conv:
                    conv.message_count += 1

                self.db.commit()

            # 更新 Agent 执行记录为 completed
            all_node_executions = orchestrator_node_executions + node_executions
            agent_exec_repo.update_completed(
                execution_id=agent_execution.id,
                steps=all_node_executions,
                status="completed",
                elapsed_time=elapsed_time,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                message_id=message_id,
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
            # 保存失败的消息，使前端可以展示失败状态
            try:
                self.conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    meta_data=human_meta,
                )
                self.conversation_service.add_message(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content="",
                    meta_data={"error": str(e)[:2000]},
                    status="failed",
                )
            except Exception:
                pass
            # 更新 Agent 执行记录为 failed
            try:
                elapsed_time = time.time() - start_time
                agent_exec_repo.update_completed(
                    execution_id=agent_execution.id,
                    steps=node_executions if 'node_executions' in dir() else [],
                    status="failed",
                    elapsed_time=elapsed_time,
                    error_message=str(e)[:2000],
                )
            except Exception:
                pass  # 保存失败不影响错误事件发送
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
                # 拦截 sub_usage 事件，累加 token
                if "event: sub_usage" in event:
                    if "data:" in event:
                        try:
                            data_line = event.split("data: ", 1)[1].strip()
                            data = json.loads(data_line)
                            total_tokens += data.get("total_tokens", 0)
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
            source: str = "",
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
            source=source,
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
            public=False,
            source: str = "",

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
                public=public,
                source=source
        ):
            yield event

    # ==================== 重新生成功能 ====================

    async def regenerate(
            self,
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成回复（非流式，多版本支持）

        Args:
            message_id: 原 AI 回复的消息ID
            config: AgentConfig
            workspace_id: 工作空间ID
            user_id: 用户ID
            variables: 变量参数
            web_search: 是否启用网络搜索
            memory: 是否启用长期记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Returns:
            Dict: 包含新消息ID、内容、版本号等
        """
        # 1. 获取原消息
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. 获取父用户消息
        parent_msg_id = original_msg.parent_message_id
        if not parent_msg_id:
            from sqlalchemy import select
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at < original_msg.created_at,
                    Message.is_deleted == False,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
            if not parent_msg:
                raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
            original_msg.parent_message_id = parent_msg.id
            self.db.commit()
            parent_msg_id = parent_msg.id
        else:
            parent_msg = self.db.get(Message, parent_msg_id)

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. 查询同一 parent_message_id 下的所有版本，获取最大版本号
        from sqlalchemy import select, func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted == False,
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. 将同一 parent_message_id 下所有版本标记为非当前
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. 提取父消息中的文件信息
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = FileInput(
                            type=FileType(f.get("type", "document")),
                            transfer_method=TransferMethod.REMOTE_URL if f.get("url") else TransferMethod.LOCAL_FILE,
                            url=f.get("url"),
                            file_type=f.get("file_type"),
                            name=f.get("name"),
                            size=f.get("size"),
                        )
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. 加载上下文（到父消息为止）
        conversation_id = original_msg.conversation_id
        max_history = config.memory.get("max_history", 10) if config.memory else 10
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=max_history
        )

        # 7. 调用 agent_chat（传入版本参数，由 agent_chat 保存）
        result = await self.agent_chat(
            message=user_message_content,
            conversation_id=conversation_id,
            config=config,
            files=files,
            user_id=user_id,
            variables=variables,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            workspace_id=str(workspace_id),
            history=filtered_history,
            skip_save=True,
            parent_message_id=parent_msg_id,
            version=new_version,
        )

        logger.info(
            "重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": result["message_id"],
                "version": new_version,
            }
        )

        return {
            "message_id": result["message_id"],
            "message": result["message"],
            "reasoning_content": result.get("reasoning_content"),
            "version": new_version,
            "conversation_id": str(conversation_id),
            "suggested_questions": result.get("suggested_questions", []),
            "citations": result.get("citations", []),
            "audio_url": result.get("audio_url"),
            "audio_status": result.get("audio_status"),
        }

    async def regenerate_stream(
            self,
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """重新生成回复（流式输出，多版本支持）

        Args:
            message_id: 原 AI 回复的消息ID
            config: AgentConfig
            workspace_id: 工作空间ID
            user_id: 用户ID
            variables: 变量参数
            web_search: 是否启用网络搜索
            memory: 是否启用长期记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Yields:
            str: SSE 格式的事件数据
        """
        from app.models import Message, Conversation
        from app.core.error_codes import BizCode
        from app.core.exceptions import BusinessException
        from app.schemas.app_schema import FileType, TransferMethod

        # 1. 获取原消息
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. 获取父用户消息
        parent_msg_id = original_msg.parent_message_id
        if not parent_msg_id:
            from sqlalchemy import select
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at < original_msg.created_at,
                    Message.is_deleted == False,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
            if not parent_msg:
                raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
            original_msg.parent_message_id = parent_msg.id
            self.db.commit()
            parent_msg_id = parent_msg.id
        else:
            parent_msg = self.db.get(Message, parent_msg_id)

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. 查询同一 parent_message_id 下的所有版本，获取最大版本号
        from sqlalchemy import select, func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted == False,
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. 将同一 parent_message_id 下所有版本标记为非当前
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. 提取父消息中的文件信息
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = FileInput(
                            type=FileType(f.get("type", "document")),
                            transfer_method=TransferMethod.REMOTE_URL if f.get("url") else TransferMethod.LOCAL_FILE,
                            url=f.get("url"),
                            file_type=f.get("file_type"),
                            name=f.get("name"),
                            size=f.get("size"),
                        )
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. 加载上下文
        conversation_id = original_msg.conversation_id
        max_history = config.memory.get("max_history", 10) if config.memory else 10
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=max_history
        )

        # 7. 流式调用（传入版本参数，由 agent_chat_stream 保存）
        async for event_str in self.agent_chat_stream(
                message=user_message_content,
                conversation_id=conversation_id,
                config=config,
                files=files,
                user_id=user_id,
                variables=variables,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                workspace_id=str(workspace_id),
                history=filtered_history,
                skip_save=True,
                parent_message_id=parent_msg_id,
                version=new_version,
        ):
            yield event_str

        logger.info(
            "重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "version": new_version,
            }
        )

    async def _load_history_before_message(
            self,
            conversation_id: uuid.UUID,
            before_time,
            max_history: int = 10
    ) -> List[Dict[str, Any]]:
        """加载指定时间之前的历史消息（用于重新生成场景）"""
        from sqlalchemy import select
        from app.models import Message

        history_msgs = self.db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_current == True,
                Message.created_at < before_time,
                Message.is_deleted == False,
            )
            .order_by(Message.created_at.asc())
            .limit(max_history)
        ).all()

        filtered_history = []
        for msg in history_msgs:
            msg_dict = {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.content}]
            }
            if msg.role == "user" and msg.meta_data:
                history_files = msg.meta_data.get("history_files", {})
                if history_files and history_files.get("content"):
                    msg_dict["content"].extend(history_files.get("content"))
            filtered_history.append(msg_dict)

        logger.debug(
            "加载指定时间前的历史消息",
            extra={"conversation_id": str(conversation_id), "loaded_count": len(filtered_history)}
        )

        return filtered_history


# ==================== 依赖注入函数 ====================

def get_app_chat_service(
        db: Annotated[Session, Depends(get_db)]
) -> AppChatService:
    """获取工作流服务（依赖注入）"""
    return AppChatService(db)
