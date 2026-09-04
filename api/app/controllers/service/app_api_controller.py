"""App 服务接口 - 基于 API Key 认证"""
import datetime
import json
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Body, Query, File, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.services.file_storage_service import (
    FileStorageService,
    get_file_storage_service,
    upload_workspace_file,
)
from app.core.api_key_auth import require_api_key, require_api_key_self_db
from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.quota_manager import check_end_user_quota_async, report_quota_change
from app.core.response_utils import success
from app.db import get_db, get_async_db_context
from app.models.app_model import AppType
from app.models.workspace_model import Workspace
from app.models.app_release_model import AppRelease
from app.models.workflow_model import WorkflowExecution
from app.repositories import knowledge_repository
from app.repositories.end_user_repository import EndUserRepository
from app.schemas import AppChatRequest, conversation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse, PageData, PageMeta
from app.services import workspace_service
from app.services.agent_config_helper import enrich_agent_config
from app.services.app_chat_service import AppChatService
from app.services.app_service import get_app_service, AppService
from app.services.conversation_service import ConversationService, get_conversation_service
from app.services.intervention_registry import submit_intervention
from app.services.workflow_service import WorkflowService
from app.utils.app_config_utils import workflow_config_4_app_release, \
    agent_config_4_app_release, multi_agent_config_4_app_release
from app.utils.redis_cache import CACHE_MISS, get_json_async, set_json_async, redis_cache

router = APIRouter(prefix="/app", tags=["V1 - App API"])
logger = get_business_logger()


@redis_cache(ttl=120, prefix="storage_type", skip_args=["db"], id_arg="workspace_id")
async def _prepare_v1_chat_memory_context_async(
        db: AsyncSession,
        workspace_id: uuid.UUID,
) -> tuple[str, str]:
    storage_type = await workspace_service.get_workspace_storage_type_without_auth_async(
        db=db,
        workspace_id=workspace_id,
    )
    if storage_type is None:
        storage_type = "neo4j"

    user_rag_memory_id = ""
    if storage_type == "rag":
        knowledge = await knowledge_repository.get_knowledge_by_name_async(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id,
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)
        else:
            logger.warning(
                f"未找到名为 'USER_RAG_MERORY' 的知识库，workspace_id: {workspace_id}，将使用 neo4j 存储"
            )
            storage_type = "neo4j"

    return storage_type, user_rag_memory_id


async def _get_or_create_v1_end_user_async(
        db: AsyncSession,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        other_id: str,
):
    end_user_repo = EndUserRepository(db)

    cache_key = f"cache:v2:end_user:{workspace_id}:{other_id}"
    cached = await get_json_async(cache_key)
    if cached is not CACHE_MISS:
        from types import SimpleNamespace
        cached_id = cached.get("id")
        if cached_id and cached.get("app_id") == str(app_id):
            return SimpleNamespace(id=uuid.UUID(cached_id), app_id=app_id)

    existing_end_user = await end_user_repo.get_end_user_by_other_id_async(
        workspace_id=workspace_id,
        other_id=other_id,
    )
    if existing_end_user is not None:
        if existing_end_user.app_id != app_id:
            existing_end_user.app_id = app_id
            await db.commit()
        await set_json_async(cache_key, {
            "id": str(existing_end_user.id),
            "app_id": str(existing_end_user.app_id),
        }, ttl=120)
        return existing_end_user

    workspace = await db.get(Workspace, workspace_id)
    # 配额查询在 premium 表缺失时会 rollback 当前 AsyncSession，并使 ORM 实例过期。
    # 在检查前保存标量，后续禁止再读取 workspace.*，避免触发 MissingGreenlet。
    workspace_tenant_id = workspace.tenant_id if workspace is not None else None
    if workspace_tenant_id is not None:
        await check_end_user_quota_async(
            db,
            workspace_tenant_id,
            workspace_id=workspace_id,
        )

    new_user = await end_user_repo.get_or_create_end_user_async(
        app_id=app_id,
        workspace_id=workspace_id,
        other_id=other_id,
    )
    # 终端用户已落库，用量真正发生变化后才评估告警。
    if workspace_tenant_id is not None:
        await report_quota_change(
            workspace_tenant_id,
            "end_user_quota",
            workspace_id=workspace_id,
        )
    await set_json_async(cache_key, {
        "id": str(new_user.id),
        "app_id": str(new_user.app_id),
    }, ttl=120)
    return new_user


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


async def _read_json_body(request: Request) -> dict:
    """解析 JSON 请求体。

    端点内手工解析 body 时，非法 JSON 会以未捕获异常落到 500；
    这里统一转成参数类业务错误（1003 / HTTP 400）。
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise BusinessException("请求体不是合法的 JSON", BizCode.INVALID_PARAMETER, cause=exc)
    if not isinstance(body, dict):
        raise BusinessException("请求体必须是 JSON 对象", BizCode.INVALID_PARAMETER)
    return body


def _get_standard_variables(variables: list, app_type: AppType) -> list:
    """统一 Agent / Workflow 变量输出格式。"""
    is_agent = app_type in (AppType.AGENT, AppType.MULTI_AGENT)
    result = []
    for raw in variables:
        v = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict[Any, Any](raw)
        if is_agent:
            ui_type = v.get("type", "string")
            data_type = "number" if ui_type == "number" else "string"
        else:
            ui_type = v.get("ui_type") or ("number" if v.get("type") == "number" else "text-input")
            data_type = v.get("type", "string")
        result.append({
            "name": v["name"],
            "display_name": v.get("display_name"),
            "type": data_type,
            "ui_type": ui_type,
            "required": v.get("required", False),
            "description": v.get("description"),
            "max_length": v.get("max_length"),
            "default": None if is_agent else v.get("default"),
            "options": None if is_agent else v.get("options"),
            "allowed_file_types": None if is_agent else v.get("allowed_file_types"),
            "max_file_count": None if is_agent else v.get("max_file_count"),
            "max_file_size_mb": None if is_agent else v.get("max_file_size_mb"),
        })
    return result


def _variables_from_release(release: AppRelease) -> list:
    """从当前发布版本快照提取应用变量定义（与公开分享 /config 接口逻辑一致）。"""
    config = _parse_release_config(release)
    if release.type == AppType.AGENT:
        cfg = agent_config_4_app_release(release)
        cfg = enrich_agent_config(cfg)
        variables = cfg.variables or config.get("variables") or []
    elif release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        variables = WorkflowService.get_start_node_variables(config)
    elif release.type == AppType.MULTI_AGENT:
        variables = config.get("variables") or []
    else:
        raise BusinessException(f"不支持的应用类型: {release.type}", BizCode.APP_TYPE_NOT_SUPPORTED)
    return _get_standard_variables(variables, release.type)


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
@require_api_key_self_db(scopes=["app"])
async def chat(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str | None = Body(None, description="聊天消息内容"),
):
    """
    Agent/Workflow 聊天接口

    - 不传 version：使用当前生效版本（current_release，回滚后为回滚目标版本）
    - 传 version=release_id：使用指定版本uuid的历史快照，例如 {"version": "{{release_id}}"}
    """
    body = await _read_json_body(request)
    payload = AppChatRequest(**body)
    request_started_at = time.perf_counter()
    request_wall_clock = datetime.datetime.now(datetime.timezone.utc)

    resource_id = api_key_auth.resource_id
    workspace_id = api_key_auth.workspace_id

    # Try App cache before opening a DB session
    app_cache_key = f"cache:v2:app:{resource_id}"
    app_cache = await get_json_async(app_cache_key)
    _app_stub = None
    _cached_release_id = None
    if app_cache is not CACHE_MISS:
        from types import SimpleNamespace
        _app_stub = SimpleNamespace(
            id=uuid.UUID(app_cache["id"]),
            type=app_cache["type"],
        )
        if app_cache.get("current_release_id"):
            _cached_release_id = uuid.UUID(app_cache["current_release_id"])

    async with get_async_db_context() as db:
        app_service = AppService(db)
        conversation_service = ConversationService(db)

        if _app_stub is not None:
            app = _app_stub
        else:
            app = await app_service.get_app_async(resource_id, workspace_id)
            await set_json_async(app_cache_key, {
                "id": str(app.id),
                "type": app.type,
                "current_release_id": str(app.current_release_id) if app.current_release_id else None,
            }, ttl=60)

        if payload.version is not None:
            active_release = await app_service.get_release_by_id_async(app.id, payload.version)
        elif _cached_release_id is not None:
            active_release = await app_service.get_release_by_id_async(app.id, _cached_release_id)
        else:
            active_release = await app_service.get_current_release_async(
                app_id=app.id,
                workspace_id=workspace_id,
            )
        if not active_release:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

        other_id = payload.user_id
        new_end_user = await _get_or_create_v1_end_user_async(
            db,
            app_id=app.id,
            workspace_id=workspace_id,
            other_id=other_id,
        )
        end_user_id = str(new_end_user.id)
        web_search = True
        memory = True
        storage_type, user_rag_memory_id = await _prepare_v1_chat_memory_context_async(
            db,
            workspace_id,
        )
        app_type = app.type
        _checkAppConfig(active_release)

        if app_type != AppType.PURE_WORKFLOW and not payload.message:
            raise BusinessException("当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER)

        original_conversation_id = payload.conversation_id
        conversation_id = None
        is_new_conversation = True
        if app_type != AppType.PURE_WORKFLOW or payload.conversation_id:
            conversation = await conversation_service.create_or_get_conversation_async(
                app_id=app.id,
                workspace_id=workspace_id,
                user_id=end_user_id,
                is_draft=False,
                conversation_id=payload.conversation_id
            )
            conversation_id = conversation.id
            is_new_conversation = not bool(original_conversation_id)

        app_id = app.id
        release_id = active_release.id if active_release else None

        runtime_config = None
        if app_type == AppType.AGENT:
            runtime_config = agent_config_4_app_release(active_release)
        elif app_type == AppType.MULTI_AGENT:
            runtime_config = multi_agent_config_4_app_release(active_release)
        elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            runtime_config = workflow_config_4_app_release(active_release)

    logger.info(
        f"[AppApiChatTiming] preprocess_ready elapsed_ms={round((time.perf_counter() - request_started_at) * 1000, 2)} "
        f"app_type={app_type} stream={payload.stream}",
        extra={
            "app_id": str(app_id),
            "conversation_id": str(conversation_id) if conversation_id else None,
            "app_type": app_type,
            "stream": payload.stream,
            "elapsed_ms": round((time.perf_counter() - request_started_at) * 1000, 2),
        },
    )

    if app_type == AppType.AGENT:

        agent_config = runtime_config

        # thinking 开关：仅当 agent 配置了 deep_thinking 且请求 thinking=True 时才启用
        if not (agent_config.model_parameters.get("deep_thinking", False) and payload.thinking):
            agent_config.model_parameters["deep_thinking"] = False

        # 流式返回
        if payload.stream:
            execution_mode = "sandbox" if settings.E2B_ENABLED else "in_process"

            # Original code path
            from app.db import AsyncSessionLocal

            stream_db = AsyncSessionLocal()
            try:
                app_chat_service = AppChatService(stream_db)
            except Exception:
                await stream_db.close()
                raise

            async def event_generator():
                try:
                    async for event in app_chat_service.agent_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            web_search=web_search,
                            config=agent_config,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            workspace_id=workspace_id,
                            files=payload.files,
                            execution_mode=execution_mode,
                    ):
                        yield event
                finally:
                    await stream_db.close()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )

        # 非流式返回
        execution_mode = "sandbox" if settings.E2B_ENABLED else "in_process"
        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.agent_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                config=agent_config,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                workspace_id=str(workspace_id),
                files=payload.files,
                execution_mode=execution_mode,
            )
        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type == AppType.MULTI_AGENT:
        # 多 Agent 流式返回
        config = runtime_config
        if payload.stream:
            from app.db import AsyncSessionLocal

            stream_db = AsyncSessionLocal()
            try:
                app_chat_service = AppChatService(stream_db)
            except Exception:
                await stream_db.close()
                raise

            async def event_generator():
                try:
                    async for event in app_chat_service.multi_agent_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            config=config,
                            web_search=web_search,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id
                    ):
                        yield event
                finally:
                    await stream_db.close()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # 多 Agent 非流式返回
        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.multi_agent_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                config=config,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id
            )

        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        config = runtime_config
        logger.info(
            f">>>>>>> TIMING_TRACE request_received conversation_id={conversation_id} "
            f"wall_clock={request_wall_clock.isoformat()}"
        )
        if payload.stream:
            from app.db import AsyncSessionLocal

            stream_db = AsyncSessionLocal()
            try:
                app_chat_service = AppChatService(stream_db)
            except Exception:
                await stream_db.close()
                raise

            async def event_generator():
                try:
                    async for event in app_chat_service.workflow_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            files=payload.files,
                            config=config,
                            web_search=web_search,
                            memory=memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            app_id=app_id,
                            workspace_id=workspace_id,
                            release_id=release_id,
                            public=True
                    ):
                        event_type = event.get("event", "message")
                        event_data = event.get("data", {})
                        sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                        yield sse_message
                finally:
                    await stream_db.close()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # workflow 非流式返回
        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.workflow_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                config=config,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                files=payload.files,
                app_id=app_id,
                workspace_id=workspace_id,
                release_id=release_id
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
    result = await conversation_service.list_v1_conversation_messages_async(
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
    app_id = _get_app_id(api_key_auth)
    execution = db.query(WorkflowExecution).filter(
        WorkflowExecution.execution_id == execution_id,
    ).first()

    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    # API Key 只能提交其绑定应用产生的人工介入，避免同工作空间跨应用越权。
    if execution.app_id != app_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    # 应用已删除或执行记录成为孤儿时，不能继续访问 relationship 属性导致 500。
    if execution.app is None:
        raise BusinessException("执行记录关联的应用不存在", BizCode.NOT_FOUND)

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
        upload_result = await upload_workspace_file(
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
    return success(data=suggested_questions)


@router.get("/info", summary="获取应用基本信息")
@require_api_key(scopes=["app"])
async def get_app_basic_info_v1(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        app_service: Annotated[AppService, Depends(get_app_service)] = None,
        version: uuid.UUID | None = Query(None, description="发布版本 ID，不传则使用当前生效版本"),
):
    """获取 API Key 绑定应用的基本信息（来自发布版本快照）。"""
    app_id = _get_app_id(api_key_auth)
    workspace_id = api_key_auth.workspace_id

    app_service.get_app(app_id, workspace_id)
    if version is not None:
        release = app_service.get_release_by_id(app_id, version)
    else:
        release = app_service.get_current_release(app_id=app_id, workspace_id=workspace_id)
        if not release:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    return success(data={
        "app_id": str(release.app_id),
        "name": release.name,
        "description": release.description,
        "icon": release.icon,
        "type": release.type,
    })
