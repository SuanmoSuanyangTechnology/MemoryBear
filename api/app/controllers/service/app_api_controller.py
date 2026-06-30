"""App 服务接口 - 基于 API Key 认证"""
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Body, Query, File, UploadFile, HTTPException
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.services.file_storage_service import (
    FileStorageService,
    get_file_storage_service,
    upload_v1_app_file,
)
from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db, get_db_context
from app.models.app_model import AppType
from app.models.workspace_model import Workspace
from app.models.app_release_model import AppRelease
from app.models.workflow_model import WorkflowExecution
from app.repositories import knowledge_repository
from app.repositories.end_user_repository import EndUserRepository
from app.schemas import AppChatRequest, app_schema, conversation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse, PageData, PageMeta
from app.schemas.human_intervention_schema import HumanInterventionSubmitRequest
from app.services import workspace_service
from app.services.agent_config_helper import enrich_agent_config
from app.services.app_chat_service import AppChatService, get_app_chat_service
from app.services.app_service import get_app_service, AppService
from app.services.conversation_service import ConversationService, get_conversation_service
from app.services.intervention_registry import submit_intervention
from app.services.workflow_service import WorkflowService
from app.utils.app_config_utils import workflow_config_4_app_release, \
    agent_config_4_app_release, multi_agent_config_4_app_release

router = APIRouter(prefix="/app", tags=["V1 - App API"])
logger = get_business_logger()


@router.get("")
async def list_apps():
    """列出可访问的应用（占位）"""
    return success(data=[], msg="App API - Coming Soon")


# /v1/app/chat

# @router.post("/chat")
# @require_api_key(scopes=["app"])
# async def chat2(
#     request: Request,
#     api_key_auth: ApiKeyAuth = None,
#     db: Session = Depends(get_db),
#     message: str = Body(..., description="聊天消息内容"),
# ):
#     """
#     Agent 聊天接口demo

#     scopes: 所需的权限范围列表["app", "rag", "memory"]

#     Args:
#         message: 请求参数
#         request: 声明请求
#         api_key_auth: 包含验证后的API Key 信息
#         db: db_session
#     """
#     logger.info(f"API Key Auth: {api_key_auth}")
#     logger.info(f"Message: {message}")
#     return success(data={"received": True}, msg="消息已接收")


def _get_app_id(api_key_auth: ApiKeyAuth) -> uuid.UUID:
    if not api_key_auth.resource_id:
        raise BusinessException("API Key 未绑定应用", BizCode.BAD_REQUEST)
    return api_key_auth.resource_id


def _checkAppConfig(release: AppRelease):
    if release.type == AppType.AGENT:
        if not release.config:
            raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif release.type == AppType.MULTI_AGENT:
        if not release.config:
            raise BusinessException("Multi-Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        if not release.config:
            raise BusinessException("工作流应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    else:
        raise BusinessException("不支持的应用类型", BizCode.APP_TYPE_NOT_SUPPORTED)


def _parse_release_config(release: AppRelease) -> dict:
    config = release.config or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}
    return config if isinstance(config, dict) else {}


def _variables_from_release(release: AppRelease) -> list:
    """从当前发布版本快照提取应用变量定义（与公开分享 /config 接口逻辑一致）。"""
    config = _parse_release_config(release)
    if release.type == AppType.AGENT:
        cfg = agent_config_4_app_release(release)
        cfg = enrich_agent_config(cfg)
        variables = app_schema.AgentConfig.model_validate(cfg).variables
        return [v.model_dump(mode="json") for v in variables]
    if release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        return WorkflowService.get_start_node_variables(config)
    if release.type == AppType.MULTI_AGENT:
        return config.get("variables") or []
    raise BusinessException(f"不支持的应用类型: {release.type}", BizCode.APP_TYPE_NOT_SUPPORTED)


@router.get("/variable", summary="获取应用变量配置")
@require_api_key(scopes=["app"])
async def get_app_variables(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        app_service: Annotated[AppService, Depends(get_app_service)] = None,
):
    """获取 API Key 绑定应用的变量定义列表（来自当前发布版本的 config 快照）。"""
    app_id = _get_app_id(api_key_auth)
    workspace_id = api_key_auth.workspace_id

    app_service.get_app(app_id, workspace_id)
    release = app_service.get_current_release(app_id=app_id, workspace_id=workspace_id)
    if not release:
        raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    return success(data=_variables_from_release(release))


@router.post("/chat")
@require_api_key(scopes=["app"])
async def chat(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
        app_chat_service: Annotated[AppChatService, Depends(get_app_chat_service)] = None,
        app_service: Annotated[AppService, Depends(get_app_service)] = None,
        message: str | None = Body(None, description="聊天消息内容"),
):
    """
    Agent/Workflow 聊天接口

    - 不传 version：使用当前生效版本（current_release，回滚后为回滚目标版本）
    - 传 version=release_id：使用指定版本uuid的历史快照，例如 {"version": "{{release_id}}"}
    """
    body = await request.json()
    payload = AppChatRequest(**body)

    app = app_service.get_app(api_key_auth.resource_id, api_key_auth.workspace_id)

    # 版本切换：指定 release_id 时查找对应历史快照，否则使用当前激活版本
    if payload.version is not None:
        active_release = app_service.get_release_by_id(app.id, payload.version)
    else:
        active_release = app.current_release
    other_id = payload.user_id
    workspace_id = api_key_auth.workspace_id
    end_user_repo = EndUserRepository(db)

    # 仅在新建终端用户时检查配额，已有用户复用不受限制
    existing_end_user = end_user_repo.get_end_user_by_other_id(workspace_id=workspace_id, other_id=other_id)
    if existing_end_user is None:
        from app.core.quota_manager import _check_quota
        from app.models.workspace_model import Workspace
        ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if ws:
            _check_quota(db, ws.tenant_id, "end_user_quota", "end_user", workspace_id=workspace_id)

    new_end_user = end_user_repo.get_or_create_end_user(
        app_id=app.id,
        workspace_id=workspace_id,
        other_id=other_id,
    )
    end_user_id = str(new_end_user.id)
    web_search = True
    memory = True
    # 提前验证和准备（在流式响应开始前完成）
    storage_type = workspace_service.get_workspace_storage_type_without_auth(
        db=db,
        workspace_id=workspace_id
    )
    if storage_type is None:
        storage_type = 'neo4j'
    user_rag_memory_id = ''
    if storage_type == 'rag':
        if workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db,
                name="USER_RAG_MERORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)
            else:
                logger.warning(
                    f"未找到名为 'USER_RAG_MERORY' 的知识库，workspace_id: {workspace_id}，将使用 neo4j 存储")
                storage_type = 'neo4j'
        else:
            logger.warning("workspace_id 为空，无法使用 rag 存储，将使用 neo4j 存储")
            storage_type = 'neo4j'
    app_type = app.type
    # check app config
    _checkAppConfig(active_release)

    if app_type != AppType.PURE_WORKFLOW and not payload.message:
        raise BusinessException("当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER)

    # pure_workflow 不强制创建会话；传入 conversation_id 时仍支持复用。
    conversation = None
    if app_type != AppType.PURE_WORKFLOW or payload.conversation_id:
        conversation = conversation_service.create_or_get_conversation(
            app_id=app.id,
            workspace_id=workspace_id,
            user_id=end_user_id,
            is_draft=False,
            conversation_id=payload.conversation_id
        )

    # 提前提取 ORM 对象属性为纯值，避免 event_generator 闭包持有 ORM 引用导致 db 连接无法释放
    _app_id = app.id
    _release_id = active_release.id if active_release else None
    _conversation_id = conversation.id if conversation else None

    if app_type == AppType.AGENT:

        # print("="*50)
        # print(app.current_release.default_model_config_id)
        agent_config = agent_config_4_app_release(active_release)
        # print(agent_config.default_model_config_id)

        # thinking 开关：仅当 agent 配置了 deep_thinking 且请求 thinking=True 时才启用
        if not (agent_config.model_parameters.get("deep_thinking", False) and payload.thinking):
            agent_config.model_parameters["deep_thinking"] = False

        # 流式返回
        if payload.stream:
            async def event_generator():
                with get_db_context() as stream_db:
                    from app.services.app_chat_service import AppChatService as _AppChatService
                    _chat_service = _AppChatService(stream_db)
                    async for event in _chat_service.agent_chat_stream(
                            message=payload.message,
                             conversation_id=_conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            web_search=web_search,
                            config=agent_config,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            workspace_id=workspace_id,
                            files=payload.files
                    ):
                        yield event

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        # 非流式返回
        result = await app_chat_service.agent_chat(
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=end_user_id,  # 转换为字符串
            variables=payload.variables,
            config=agent_config,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            workspace_id=str(workspace_id),
            files=payload.files  # 传递多模态文件
        )
        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type == AppType.MULTI_AGENT:
        # 多 Agent 流式返回
        config = multi_agent_config_4_app_release(active_release)
        if payload.stream:
            async def event_generator():
                with get_db_context() as stream_db:
                    from app.services.app_chat_service import AppChatService as _AppChatService
                    _chat_service = _AppChatService(stream_db)
                    async for event in _chat_service.multi_agent_chat_stream(
                            message=payload.message,
                             conversation_id=_conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            config=config,
                            web_search=web_search,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id
                    ):
                        yield event

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        # 多 Agent 非流式返回
        result = await app_chat_service.multi_agent_chat(
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=end_user_id,  # 转换为字符串
            variables=payload.variables,
            config=config,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )

        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        # 多 Agent 流式返回
        config = workflow_config_4_app_release(active_release)
        if payload.stream:
            async def event_generator():
                with get_db_context() as stream_db:
                    from app.services.app_chat_service import AppChatService as _AppChatService
                    _chat_service = _AppChatService(stream_db)
                    async for event in _chat_service.workflow_chat_stream(
                            message=payload.message,
                             conversation_id=_conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            files=payload.files,
                            config=config,
                            web_search=web_search,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                             app_id=_app_id,
                            workspace_id=workspace_id,
                             release_id=_release_id,
                            public=True
                    ):
                        event_type = event.get("event", "message")
                        event_data = event.get("data", {})
                        sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                        yield sse_message

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        # workflow 非流式返回
        result = await app_chat_service.workflow_chat(

            message=payload.message,
            conversation_id=conversation.id if conversation else None,
            user_id=end_user_id,  # 转换为字符串
            variables=payload.variables,
            config=config,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            files=payload.files,
            app_id=app.id,
            workspace_id=workspace_id,
            release_id=active_release.id
        )
        logger.debug(
            "工作流试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "has_response": "response" in result if isinstance(result, dict) else False
            }
        )
        return success(
            data=result,
            msg="工作流任务执行成功"
        )
    else:
        raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)


@router.get("/conversations")
@require_api_key(scopes=["app"])
async def list_v1_conversations(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
        user_id: str = Query("", description="外部系统用户 ID"),
        page: int = Query(1, description="页码，从 1 开始"),
        page_size: int = Query(20, description="每页数量，最大 100"),
):
    """获取当前应用下指定外部用户的会话列表。"""
    result = conversation_service.list_v1_conversations(
        app_id=api_key_auth.resource_id,
        workspace_id=api_key_auth.workspace_id,
        external_user_id=user_id,
        page=page,
        page_size=page_size,
    )
    items = [
        conversation_schema.V1ConversationListItem(**item)
        for item in result["items"]
    ]
    page_meta = PageMeta(
        page=result["page"],
        pagesize=result["page_size"],
        total=result["total"],
        hasnext=result["hasnext"],
    )
    return success(data=PageData(page=page_meta, items=items).model_dump(mode="json"))


@router.get("/conversations/{conversation_id}/messages")
@require_api_key(scopes=["app"])
async def list_v1_conversation_messages(
        request: Request,
        conversation_id: uuid.UUID,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
        user_id: str = Query("", description="外部系统用户 ID"),
        limit: int = Query(20, description="返回消息数量，最大 200"),
):
    """获取当前应用下指定会话的历史消息。"""
    result = conversation_service.list_v1_conversation_messages(
        app_id=api_key_auth.resource_id,
        workspace_id=api_key_auth.workspace_id,
        external_user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    return success(data=conversation_schema.V1ConversationMessageListResponse(**result).model_dump(mode="json"))


@router.post(
    "/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（API Key 认证，通知 SSE 流继续执行）",
)
@require_api_key(scopes=["app"])
async def submit_human_intervention_api(
        request: Request,
        execution_id: str,
        node_id: str = Body(..., description="人工介入节点 ID"),
        action_id: str = Body(..., description="用户触发的操作 ID"),
        form_data: dict | None = Body(default=None, description="用户填写的表单数据"),
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
):
    app_id = api_key_auth.resource_id
    # Query by execution_id (unique key) first, then validate app relationship.
    # Using a combined filter (execution_id + app_id) can fail when the API Key's
    # resource_id doesn't match the app_id stored in the execution record
    # (e.g. API Key resource_id points to a release rather than the app directly).
    execution = db.query(WorkflowExecution).filter(
        WorkflowExecution.execution_id == execution_id,
    ).first()

    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    # Validate that the execution belongs to the API Key's workspace
    if execution.app.workspace_id != api_key_auth.workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    if execution.status != "waiting_human":
        raise BusinessException(
            f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
            BizCode.BAD_REQUEST,
        )

    result = submit_intervention(execution_id, node_id, action_id, form_data)
    if not result:
        raise BusinessException(
            "未找到等待中的干预请求，可能 SSE 连接已断开",
            BizCode.BAD_REQUEST,
        )

    return success(data={
        "execution_id": execution_id,
        "node_id": node_id,
        "action_id": action_id,
        "form_data": form_data,
    })


@router.post("/files", response_model=ApiResponse, summary="AI 对话文件上传")
@require_api_key(scopes=["app"])
async def upload_chat_file(
        request: Request,
        file: UploadFile = File(...),
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    上传文件到存储后端，供 /chat 接口多模态对话使用。

    - 入参: multipart/form-data，字段 file
    - 出参: {"file_id": "...", "file_key": "..."}
    - 在 /chat 请求中通过 files 字段引用，例如:
      {"type": "image", "transfer_method": "local_file", "upload_file_id": "<file_id>"}
    """
    app_id = _get_app_id(api_key_auth)

    workspace = db.query(Workspace).filter(Workspace.id == api_key_auth.workspace_id).first()
    if not workspace:
        raise BusinessException("Workspace not found", BizCode.NOT_FOUND)

    logger.info(
        f"V1 app file upload: app_id={app_id}, "
        f"workspace_id={api_key_auth.workspace_id}, filename={file.filename}"
    )

    try:
        upload_result = await upload_v1_app_file(
            db=db,
            tenant_id=workspace.tenant_id,
            workspace_id=api_key_auth.workspace_id,
            file=file,
            storage_service=storage_service,
        )
    except HTTPException as exc:
        logger.error(f"Storage upload failed: {exc}")
        raise

    logger.info(f"File uploaded to storage: file_key={upload_result['file_key']}")
    logger.info(
        f"File upload successful: {file.filename} (file_id: {upload_result['file_id']})"
    )

    return success(data=upload_result, msg="File upload successful")


@router.get("/messages/{message_id}/suggested", summary="获取消息预制问题")
@require_api_key(scopes=["app"])
async def get_message_suggested_questions_v1(
        message_id: uuid.UUID,
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
):
    """获取指定消息的预制问题列表（来自 messages.meta_data.suggested_questions）。"""
    app_id = _get_app_id(api_key_auth)
    logger.info(
        f"V1 get message suggested questions - message_id: {message_id}, "
        f"app_id: {app_id}, workspace: {api_key_auth.workspace_id}"
    )

    suggested_questions = conversation_service.get_v1_message_suggested_questions(
        app_id=app_id,
        workspace_id=api_key_auth.workspace_id,
        message_id=message_id,
    )
    return {"result": "success", "data": suggested_questions}


@router.get("/info", summary="获取应用基本信息")
@require_api_key(scopes=["app"])
async def get_app_basic_info_v1(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        app_service: Annotated[AppService, Depends(get_app_service)] = None,
):
    """获取 API Key 绑定应用的当前发布版本信息（数据来自 app_releases 快照，含 config）。"""
    app_id = _get_app_id(api_key_auth)
    workspace_id = api_key_auth.workspace_id

    logger.info(f"V1 get app basic info - app_id: {app_id}, workspace: {workspace_id}")

    app_service.get_app(app_id, workspace_id)
    release = app_service.get_current_release(app_id=app_id, workspace_id=workspace_id)
    if not release:
        raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)
    return success(data=app_schema.AppRelease.model_validate(release).model_dump(mode="json"))
