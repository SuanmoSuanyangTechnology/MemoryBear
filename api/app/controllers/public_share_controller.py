import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.quota_manager import check_end_user_quota
from app.core.response_utils import success, fail
from app.core.utils.datetime_utils import parse_iso_to_utc_naive, to_timestamp_ms
from app.db import get_db, get_db_read
from app.dependencies import get_share_user_id, ShareTokenData
from app.models.annotation_model import HitLogSource
from app.models.app_model import AppType
from app.models.workflow_model import WorkflowExecution
from app.repositories import knowledge_repository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.workflow_repository import WorkflowConfigRepository
from app.schemas import release_share_schema, conversation_schema, app_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services import workspace_service
from app.services.app_chat_service import AppChatService, get_app_chat_service
from app.services.app_service import AppService
from app.services.auth_service import create_access_token
from app.services.conversation_service import ConversationService
from app.services.conversation_share_service import ConversationShareService
from app.services.message_report_service import ReportService
from app.services.release_share_service import ReleaseShareService
from app.services.shared_chat_service import SharedChatService
from app.services.workflow_service import WorkflowService
from app.models.file_metadata_model import FileMetadata
from app.utils.app_config_utils import workflow_config_4_app_release, \
    agent_config_4_app_release, multi_agent_config_4_app_release
from app.services.message_feedback_service import FeedbackService
from app.models import Message

router = APIRouter(prefix="/public/share", tags=["Public Share"])
logger = get_business_logger()


def _to_ms(iso_str: str | None) -> int | None:
    """将 ISO 8601 时间字符串转换为毫秒时间戳，失败返回 None"""
    if not iso_str:
        return None
    try:
        return to_timestamp_ms(parse_iso_to_utc_naive(iso_str))
    except (ValueError, TypeError):
        return None


def get_base_url(request: Request) -> str:
    """从请求中获取基础 URL"""
    return f"{request.url.scheme}://{request.url.netloc}"


def get_or_generate_user_id(payload_user_id: str, request: Request) -> str:
    """获取或生成用户 ID

    优先级：
    1. 使用前端传递的 user_id
    2. 基于 IP + User-Agent 生成唯一 ID

    Args:
        payload_user_id: 前端传递的 user_id
        request: FastAPI Request 对象

    Returns:
        用户 ID
    """
    if payload_user_id:
        return payload_user_id

    # 获取客户端 IP
    client_ip = request.client.host if request.client else "unknown"

    # 获取 User-Agent
    user_agent = request.headers.get("user-agent", "unknown")

    # 生成唯一 ID：基于 IP + User-Agent 的哈希
    unique_string = f"{client_ip}_{user_agent}"
    hash_value = hashlib.md5(unique_string.encode()).hexdigest()[:16]

    return f"guest_{hash_value}"


@router.post(
    "/{share_token}/token",
    summary="获取访问 token"
)
def get_access_token(
        share_token: str,
        payload: release_share_schema.TokenRequest,
        request: Request,
        db: Session = Depends(get_db),
):
    """获取访问 token

    - 用户通过 user_id + share_token 换取访问 token
    - 后续请求需要携带此 token
    """
    # 获取或生成 user_id
    user_id = get_or_generate_user_id(payload.user_id, request)

    # 验证分享链接（可选：验证密码）
    service = ReleaseShareService(db)
    try:
        service.get_shared_release_info(
            share_token=share_token,
            password=payload.password
        )
    except Exception as e:
        logger.error(f"获取分享信息失败: {str(e)}")
        raise

    # 生成 token
    access_token = create_access_token(user_id, share_token)

    logger.info(
        "生成访问 token",
        extra={
            "share_token": share_token,
            "user_id": user_id
        }
    )

    return success(data={
        "access_token": access_token,
        "token_type": "Bearer",
        "user_id": user_id
    })


@router.get(
    "",
    summary="获取公开分享的应用信息",
    response_model=None
)
def get_shared_release(
        password: str = Query(None, description="访问密码（如果需要）"),
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """获取公开分享的发布版本信息

    - 无需认证即可访问
    - 如果设置了密码保护，需要提供正确的密码
    - 如果密码错误或未提供密码，返回基本信息（不含配置详情）
    """
    service = ReleaseShareService(db)
    info = service.get_shared_release_info(
        share_token=share_data.share_token,
        password=password
    )

    return success(data=info)


@router.post(
    "/verify",
    summary="验证访问密码"
)
def verify_password(
        payload: release_share_schema.PasswordVerifyRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """验证分享的访问密码

    - 用于前端先验证密码，再获取完整信息
    """
    service = ReleaseShareService(db)
    is_valid = service.verify_password(
        share_token=share_data.share_token,
        password=payload.password
    )

    return success(data={"valid": is_valid})


@router.get(
    "/embed",
    summary="获取嵌入代码"
)
def get_embed_code(
        width: str = Query("100%", description="iframe 宽度"),
        height: str = Query("600px", description="iframe 高度"),
        request: Request = None,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """获取嵌入代码

    - 返回 iframe 嵌入代码
    - 可以自定义宽度和高度
    """
    base_url = get_base_url(request) if request else None

    service = ReleaseShareService(db)
    embed_code = service.get_embed_code(
        share_token=share_data.share_token,
        width=width,
        height=height,
        base_url=base_url
    )

    return success(data=embed_code)


# ---------- 会话管理接口 ----------

@router.get(
    "/conversations",
    summary="获取会话列表"
)
def list_conversations(
        password: str = Query(None, description="访问密码"),
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=100),
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """获取分享应用的会话列表

    - 可以按 user_id 筛选
    - 支持分页
    """
    logger.debug(f"share_data:{share_data.user_id}")
    other_id = share_data.user_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, password)
    end_user_repo = EndUserRepository(db)
    app_service = AppService(db)
    app = app_service._get_app_or_404(share.app_id)
    workspace_id = app.workspace_id

    # 仅在新建终端用户时检查配额
    existing_end_user = end_user_repo.get_end_user_by_other_id(workspace_id=workspace_id, other_id=other_id)
    if existing_end_user is None:
        from app.core.quota_manager import _check_quota
        from app.models.workspace_model import Workspace
        ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if ws:
            _check_quota(db, ws.tenant_id, "end_user_quota", "end_user", workspace_id=workspace_id)

    new_end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=other_id
    )
    logger.debug(new_end_user.id)
    conversations, total = service.list_conversations(
        share_token=share_data.share_token,
        user_id=str(new_end_user.id),
        password=password,
        page=page,
        pagesize=pagesize
    )

    items = [conversation_schema.Conversation.model_validate(c) for c in conversations]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)

    return success(data=PageData(page=meta, items=items))


@router.get(
    "/conversations/{conversation_id}",
    summary="获取会话详情（含消息）"
)
def get_conversation(
        conversation_id: uuid.UUID,
        password: str = Query(None, description="访问密码"),
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """获取会话详情和消息历史"""
    chat_service = SharedChatService(db)
    conversation = chat_service.get_conversation_messages(
        share_token=share_data.share_token,
        conversation_id=conversation_id,
        password=password
    )

    # 获取所有消息（包括多版本）
    conv_service = ConversationService(db)
    messages = conv_service.get_conversation_with_messages(conversation_id)

    file_ids = []
    message_file_id_map = {}

    # 第一次遍历：解析 audio_url，收集所有有效的 file_id
    for idx, m in enumerate(messages):
        # 处理多版本列表的情况
        if isinstance(m, list):
            # assistant 多版本列表
            for ver_idx, ver_msg in enumerate(m):
                if ver_msg.role == "assistant" and ver_msg.meta_data:
                    audio_url = ver_msg.meta_data.get("audio_url")
                    if not audio_url:
                        continue
                    try:
                        file_id = uuid.UUID(audio_url.rstrip("/").split("/")[-1])
                    except (ValueError, IndexError):
                        ver_msg.meta_data["audio_status"] = "unknown"
                        continue
                    file_ids.append(file_id)
                    message_file_id_map[(idx, ver_idx)] = file_id
        else:
            # 单条消息
            if m.role == "assistant" and m.meta_data:
                audio_url = m.meta_data.get("audio_url")
                if not audio_url:
                    continue
                try:
                    file_id = uuid.UUID(audio_url.rstrip("/").split("/")[-1])
                except (ValueError, IndexError):
                    m.meta_data["audio_status"] = "unknown"
                    continue
                file_ids.append(file_id)
                message_file_id_map[(idx, None)] = file_id

    # 批量查询所有相关的 FileMetadata
    file_status_map = {}
    if file_ids:
        file_metas = (
            db.query(FileMetadata)
            .filter(FileMetadata.id.in_(set(file_ids)))
            .all()
        )
        file_status_map = {fm.id: fm.status for fm in file_metas}

    # 第二次遍历：将查询结果映射回消息
    for (idx, ver_idx), file_id in message_file_id_map.items():
        status = file_status_map.get(file_id, "unknown")
        if ver_idx is not None:
            # 多版本列表
            messages[idx][ver_idx].meta_data["audio_status"] = status
        else:
            # 单条消息
            messages[idx].meta_data["audio_status"] = status

    # 构建消息响应列表
    message_responses = []
    for m in messages:
        if isinstance(m, list):
            # assistant 多版本列表
            version_responses = []
            for ver_msg in m:
                version_responses.append(conversation_schema.Message(
                    id=ver_msg.id,
                    conversation_id=ver_msg.conversation_id,
                    role=ver_msg.role,
                    content=ver_msg.content,
                    status=ver_msg.status,
                    meta_data=ver_msg.meta_data,
                    created_at=ver_msg.created_at,
                    feedback_type=ver_msg.feedbacks[0].feedback_type if ver_msg.feedbacks else None,
                    feedback_content=ver_msg.feedbacks[0].feedback_content if ver_msg.feedbacks else None,
                    version=ver_msg.version,
                    is_current=ver_msg.is_current,
                    parent_message_id=ver_msg.parent_message_id,
                ))
            message_responses.append(version_responses)
        else:
            # 单条消息（user 或单个 assistant）
            message_responses.append(conversation_schema.Message(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=m.content,
                status=m.status,
                meta_data=m.meta_data,
                created_at=m.created_at,
                feedback_type=m.feedbacks[0].feedback_type if m.feedbacks else None,
                feedback_content=m.feedbacks[0].feedback_content if m.feedbacks else None,
                version=m.version,
                is_current=m.is_current,
                parent_message_id=m.parent_message_id,
            ))

    conv_dict = conversation_schema.Conversation.model_validate(conversation).model_dump(mode="json")
    conv_dict["messages"] = message_responses

    # Aggregate human-intervention data across all executions in this
    # conversation. Structure: { message_id: { execution_id, status, interventions } }
    # - `interventions` is the MERGED list of (resolved_interventions ∪ interventions)
    #   for that execution, so multiple intervention nodes triggered within the
    #   same waiting_human cycle (or across resume cycles) are ALL preserved
    #   instead of being overwritten by the latest one.
    intervention_map: dict[str, dict] = {}
    for wf_exec in db.query(WorkflowExecution).filter(
        WorkflowExecution.conversation_id == conversation_id,
    ).order_by(WorkflowExecution.created_at.asc()):
        intr_ctx = (wf_exec.context or {}).get("human_intervention", {})
        if not intr_ctx:
            continue
        message_id = intr_ctx.get("message_id")
        if not message_id:
            continue

        resolved_list = intr_ctx.get("resolved_interventions") or []
        pending_list = intr_ctx.get("interventions") or []

        # Merge by node_id. Order: resolved first, then pending overlays
        # non-resolved fields. Crucially, pending data must NOT clobber a
        # already-resolved action_id/form_data with null — the resolved
        # data wins for those fields.
        merged_by_node: dict[str, dict] = {i["node_id"]: dict(i) for i in resolved_list if i.get("node_id")}
        for i in pending_list:
            nid = i.get("node_id")
            if not nid:
                continue
            base = merged_by_node.get(nid, {})
            merged = dict(base)
            for k, v in i.items():
                if k in ("resolved_action_id", "resolved_form_data", "resolved_at", "resolved_kind"):
                    # Only overlay if the base has no resolved value yet,
                    # so a stale pending snapshot doesn't wipe resolved data.
                    if base.get(k) in (None, "", []):
                        merged[k] = v
                else:
                    merged[k] = v
            merged_by_node[nid] = merged

        # Stable order: resolved (by resolved_at) first, then never-resolved
        # pending nodes at the end.
        def _sort_key(item: dict):
            return (
                item.get("resolved_at") or "9999-12-31T23:59:59",
                item.get("node_id") or "",
            )
        ordered = sorted(merged_by_node.values(), key=_sort_key)

        def _public_resolved_kind(kind: str | None):
            if kind == "pending":
                return "interrupt"
            return kind

        intervention_map[message_id] = {
            "execution_id": wf_exec.execution_id,
            "status": wf_exec.status,
            "interventions": [{
                "node_id": i["node_id"],
                "node_name": i.get("node_name", ""),
                "rendered_content": i.get("rendered_content", ""),
                "form_fields": i.get("form_fields", []),
                "actions": i.get("actions", []),
                "timeout_at": _to_ms(i.get("timeout_at")),
                "resolved_action_id": i.get("resolved_action_id"),
                "resolved_form_data": i.get("resolved_form_data"),
                "resolved_at": i.get("resolved_at"),
                "resolved_kind": _public_resolved_kind(i.get("resolved_kind")),
            } for i in ordered],
        }

    conv_dict["pending_intervention"] = intervention_map

    return success(data=conv_dict)


# ---------- 聊天接口 ----------

@router.post(
    "/chat",
    summary="发送消息（支持流式和非流式）"
)
async def chat(
        payload: conversation_schema.ChatRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
        app_chat_service: Annotated[AppChatService, Depends(get_app_chat_service)] = None,
):
    """发送消息并获取回复

    使用 Bearer token 认证：
    - Header: Authorization: Bearer {token}
    - user_id 和 share_token 从 token 中解码

    - 支持多轮对话（提供 conversation_id）
    - 支持流式返回（设置 stream=true）
    - 如果不提供 conversation_id，会自动创建新会话
    """
    service = SharedChatService(db)

    # 从依赖中获取 user_id 和 share_token
    user_id = share_data.user_id
    share_token = share_data.share_token
    password = None  # Token 认证不需要密码
    # end_user_id = user_id
    other_id = user_id

    # 提前验证和准备（在流式响应开始前完成）
    # 这样可以确保错误能正确返回，而不是在流式响应中间出错

    try:
        # 验证分享链接和密码
        share, release = service.get_release_by_share_token(share_token, password)

        # # Create end_user_id by concatenating app_id with user_id
        # end_user_id = f"{share.app_id}_{user_id}"

        # Store end_user_id in database with original user_id
        end_user_repo = EndUserRepository(db)
        app_service = AppService(db)
        app = app_service._get_app_or_404(share.app_id)
        workspace_id = app.workspace_id

        # 仅在新建终端用户时检查配额，已有用户复用不受限制
        existing_end_user = end_user_repo.get_end_user_by_other_id(workspace_id=workspace_id, other_id=other_id)
        logger.info(f"终端用户配额检查: workspace_id={workspace_id}, other_id={other_id}, existing={existing_end_user is not None}")
        if existing_end_user is None:
            from app.core.quota_manager import _check_quota
            from app.models.workspace_model import Workspace
            ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if ws:
                logger.info(f"新终端用户，执行配额检查: tenant_id={ws.tenant_id}")
                _check_quota(db, ws.tenant_id, "end_user_quota", "end_user", workspace_id=workspace_id)

        new_end_user = end_user_repo.get_or_create_end_user(
            app_id=share.app_id,
            workspace_id=workspace_id,
            other_id=other_id,
            original_user_id=user_id
        )

        # Only extract and set memory_config_id when the end user doesn't have one yet
        if not new_end_user.memory_config_id:
            from app.services.memory_config_service import MemoryConfigService
            memory_config_service = MemoryConfigService(db)
            memory_config_id, _ = memory_config_service.extract_memory_config_id(release.type, release.config or {})
            if memory_config_id:
                new_end_user.memory_config_id = memory_config_id
                db.commit()
                db.refresh(new_end_user)
        end_user_id = str(new_end_user.id)

        # appid = share.app_id
        """获取存储类型和工作空间的ID"""

        # 直接通过 SQLAlchemy 查询 app（仅查询未删除的应用）
        # app = db.query(App).filter(
        #     App.id == appid,
        #     App.is_active.is_(True)
        # ).first()
        # if not app:
        #     raise BusinessException("应用不存在", BizCode.APP_NOT_FOUND)

        # workspace_id = app.workspace_id

        # 直接从 workspace 获取 storage_type（公开分享场景无需权限检查）
        storage_type = workspace_service.get_workspace_storage_type_without_auth(
            db=db,
            workspace_id=workspace_id
        )
        if storage_type is None:
            storage_type = 'neo4j'
        user_rag_memory_id = ''

        # 如果 storage_type 是 rag，必须确保有有效的 user_rag_memory_id
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

        # 获取应用类型
        app_type = release.app.type if release.app else None

        # 根据应用类型验证配置
        if app_type == AppType.AGENT:
            # Agent 类型：验证模型配置
            model_config_id = release.default_model_config_id
            if not model_config_id:
                raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
        elif app_type == AppType.MULTI_AGENT:
            # Multi-Agent 类型：验证多 Agent 配置
            config = release.config or {}
            if not config.get("sub_agents"):
                raise BusinessException("多 Agent 应用未配置子 Agent", BizCode.AGENT_CONFIG_MISSING)
        elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            # Multi-Agent 类型：验证多 Agent 配置
            pass
        else:
            raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)

        if app_type != AppType.PURE_WORKFLOW and not payload.message:
            raise BusinessException("当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER)

        # pure_workflow 无需自动创建会话；传入 conversation_id 时仍允许沿用原会话。
        conversation = None
        if app_type != AppType.PURE_WORKFLOW or payload.conversation_id:
            conversation = service.create_or_get_conversation(
                share_token=share_data.share_token,
                conversation_id=payload.conversation_id,
                user_id=str(new_end_user.id),  # 转换为字符串
                password=password
            )

        logger.debug(
            "参数验证完成",
            extra={
                "share_token": share_token,
                "app_type": app_type,
                "conversation_id": str(conversation.id) if conversation else None,
                "stream": payload.stream
            }
        )

    except Exception as e:
        # 验证失败，直接抛出异常（会被 FastAPI 的异常处理器捕获）
        logger.error(f"参数验证失败: {str(e)}")
        raise

    if app_type == AppType.AGENT:
        # 流式返回
        agent_config = agent_config_4_app_release(release)

        if not (agent_config.model_parameters.get("deep_thinking", False) and payload.thinking):
            agent_config.model_parameters["deep_thinking"] = False

        if payload.stream:
            source = HitLogSource.EXTERNAL
            async def event_generator():
                async for event in app_chat_service.agent_chat_stream(
                        message=payload.message,
                        conversation_id=conversation.id,  # 使用已创建的会话 ID
                        user_id=str(new_end_user.id),  # 转换为字符串
                        variables=payload.variables,
                        web_search=payload.web_search,
                        config=agent_config,
                        memory=payload.memory,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        workspace_id=workspace_id,
                        files=payload.files,  # 传递多模态文件
                        source=source
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
        source = HitLogSource.EXTERNAL
        result = await app_chat_service.agent_chat(
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=str(new_end_user.id),  # 转换为字符串
            variables=payload.variables,
            config=agent_config,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            workspace_id=workspace_id,
            files=payload.files,  # 传递多模态文件
            source=source
        )
        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type == AppType.MULTI_AGENT:
        # config = workflow_config_4_app_release(release)
        config = multi_agent_config_4_app_release(release)
        if payload.stream:
            async def event_generator():
                async for event in app_chat_service.multi_agent_chat_stream(

                        message=payload.message,
                        conversation_id=conversation.id,  # 使用已创建的会话 ID
                        user_id=str(new_end_user.id),  # 转换为字符串
                        variables=payload.variables,
                        config=config,
                        web_search=payload.web_search,
                        memory=payload.memory,
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
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )

        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        config = workflow_config_4_app_release(release)
        if not config.id:
            with get_db_read() as db:
                source_config = WorkflowConfigRepository(db).get_by_app_id(release.app_id)
                config.id = source_config.id
        config.id = uuid.UUID(config.id)
        if payload.stream:
            source = HitLogSource.EXTERNAL
            async def event_generator():
                async for event in app_chat_service.workflow_chat_stream(
                        message=payload.message,
                        conversation_id=conversation.id if conversation else None,
                        user_id=end_user_id,  # 转换为字符串
                        variables=payload.variables,
                        files=payload.files,
                        config=config,
                        web_search=payload.web_search,
                        memory=payload.memory,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        app_id=release.app_id,
                        workspace_id=workspace_id,
                        release_id=release.id,
                        public=True,
                        source=source
                ):
                    event_type = event.get("event", "message")
                    event_data = event.get("data", {})

                    # 转换为标准 SSE 格式（字符串）
                    sse_message = f"event: {event_type}\ndata: {json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
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

        # 多 Agent 非流式返回
        source = HitLogSource.EXTERNAL
        result = await app_chat_service.workflow_chat(
            message=payload.message,
            conversation_id=conversation.id if conversation else None,
            user_id=end_user_id,  # 转换为字符串
            variables=payload.variables,
            files=payload.files,
            config=config,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            app_id=release.app_id,
            workspace_id=workspace_id,
            release_id=release.id,
            source=source
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
        # return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))

    else:
        raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)


@router.post(
    "/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（分享链接，通知 SSE 流继续执行）",
)
async def submit_shared_human_intervention(
        execution_id: str,
        payload: dict,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    from app.services.intervention_registry import submit_intervention
    
    node_id = payload.get("node_id", "")
    action_id = payload.get("action_id", "")
    form_data = payload.get("form_data")
    if not node_id:
        raise BusinessException("node_id 不能为空", BizCode.BAD_REQUEST)
    if not action_id:
        raise BusinessException("action_id 不能为空", BizCode.BAD_REQUEST)

    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token)

    workflow_service = WorkflowService(db)
    execution = workflow_service.get_execution(execution_id)
    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    # Validate that the execution belongs to the shared app's workspace
    # (the release's app_id might differ from execution.app_id due to release/app ID mapping)
    if execution.app.workspace_id != release.app.workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    if execution.status != "waiting_human":
        raise BusinessException(
            f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
            BizCode.BAD_REQUEST,
        )

    if not submit_intervention(execution_id, node_id, action_id, form_data):
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


@router.post(
    "/workflow/interventions/{execution_id}/resume-submit",
    summary="恢复 SSE 流并提交人工介入响应（页面刷新后使用）",
)
async def resume_and_submit_intervention(
        execution_id: str,
        payload: dict,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
        app_chat_service: Annotated[AppChatService, Depends(get_app_chat_service)] = None,
):
    """Resume an interrupted workflow SSE stream and submit the user's action.

    Use this endpoint when the original SSE stream was lost (page refresh,
    connection drop). It re-establishes the SSE stream, pushes the user's
    action, and returns a new StreamingResponse with the resumed workflow events.
    """
    node_id = payload.get("node_id", "")
    action_id = payload.get("action_id", "")
    form_data = payload.get("form_data")
    if not node_id:
        raise BusinessException("node_id 不能为空", BizCode.BAD_REQUEST)
    if not action_id:
        raise BusinessException("action_id 不能为空", BizCode.BAD_REQUEST)

    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token)

    workflow_service = WorkflowService(db)
    execution = workflow_service.get_execution(execution_id)
    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    # Validate workspace relationship instead of direct app_id comparison
    if execution.app.workspace_id != release.app.workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    intervention_ctx = (execution.context or {}).get("human_intervention", {})
    start_event_data = {
        "conversation_id": intervention_ctx.get("conversation_id"),
        "message_id": intervention_ctx.get("message_id"),
        "execution_id": execution_id,
    }

    async def event_generator():
        yield f"event: start\ndata: {json.dumps(start_event_data, default=str, ensure_ascii=False)}\n\n"

        async for event in app_chat_service.workflow_resume_intervention_stream(
            execution_id=execution_id,
            app_id=execution.app_id,  # Use the execution's actual app_id, not release.app_id
            node_id=node_id,
            action_id=action_id,
            form_data=form_data,
            public=True,
        ):
            event_type = event.get("event", "message")
            event_data = event.get("data", {})
            sse_message = f"event: {event_type}\ndata: {json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
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


@router.get("/config", summary="获取应用启动配置")
async def config_query(
        password: str = Query(None, description="访问密码"),
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token, password)
    if release.app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        workflow_service = WorkflowService(db)
        content = {
            "app_name": release.app.name,
            "app_type": release.app.type,
            "variables": workflow_service.get_start_node_variables(release.config),
            "memory":  workflow_service.is_memory_enable(release.config),
            "features": release.config.get("features")
        }
    elif release.app.type == AppType.AGENT:
        content = {
            "app_name": release.app.name,
            "app_type": release.app.type,
            "variables": release.config.get("variables"),
            "memory": release.config.get("memory", {}).get("enabled"),
            "features": release.config.get("features"),
            "model_parameters": release.config.get("model_parameters")
        }
    elif release.app.type == AppType.MULTI_AGENT:
        content = {
            "app_name": release.app.name,
            "app_type": release.app.type,
            "variables": [],
            "features": release.config.get("features")
        }
    else:
        return fail(msg="Unsupported app type", code=BizCode.APP_TYPE_NOT_SUPPORTED)
    return success(data=content)


# ---------- 消息反馈接口 ----------

@router.post(
    "/messages/{message_id}/feedback",
    summary="提交消息反馈"
)
async def submit_message_feedback(
        message_id: uuid.UUID,
        payload: app_schema.MessageFeedbackRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """点赞/点踩 AI 回复（公开分享场景）

    幂等设计：重复点击可切换取消
    - 点赞：用户对满意的 AI 回复给予正面反馈
    - 点踩：用户对不满意的 AI 回复给予负面反馈，可附加文字说明
    """
    # 验证消息存在
    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    feedback_service = FeedbackService(db)
    result = feedback_service.submit_feedback(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=share_data.user_id,
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )

    return success(data=app_schema.MessageFeedbackResponse(**result))


@router.get(
    "/messages/{message_id}/feedback",
    summary="获取用户对消息的反馈"
)
async def get_user_feedback(
        message_id: uuid.UUID,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """获取当前用户对某条消息的反馈状态"""
    feedback_service = FeedbackService(db)
    result = feedback_service.get_user_feedback(message_id, share_data.user_id)

    return success(data=result)


@router.post("/conversations/{conversation_id}/share", summary="分享会话")
async def share_conversation(
        conversation_id: uuid.UUID,
        payload: app_schema.ShareConversationRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """生成会话分享链接

    分享链接：生成可公开访问的对话链接，他人点击即可查看完整对话内容（只读视角）
    """
    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id
    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id
    )

    share_service = ConversationShareService(db)
    result = share_service.create_share(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        user_id=end_user.id,
        password=payload.password,
        expire_hours=payload.expire_hours,
        allow_copy=payload.allow_copy,
    )
    return success(data=app_schema.ShareConversationResponse(**result))


@router.delete("/conversations/share/{share_uuid}", summary="撤销分享链接")
async def revoke_share(
        share_uuid: str,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """撤销会话分享链接"""
    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    share_service = ConversationShareService(db)
    share_service.revoke_share(share_uuid, workspace_id)

    return success(msg="分享链接已撤销")


@router.get("/conversations/{conversation_id}/shares", summary="列出会话的分享链接")
async def list_conversation_shares(
        conversation_id: uuid.UUID,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """列出会话的所有分享链接"""
    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    share_service = ConversationShareService(db)
    result = share_service.list_shares(conversation_id, workspace_id)

    return success(data=result)


@router.delete("/messages/{message_id}", summary="删除消息")
async def delete_message(
        message_id: uuid.UUID,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """删除单条消息（逻辑删除）

    删除中间某条消息后，后续会话上下文自动断层，新追问不再携带被删除的消息
    """
    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    conv_service = ConversationService(db)
    await conv_service.delete_message(message_id, workspace_id)

    return success(msg="消息已删除")


@router.post("/messages/{message_id}/report", summary="举报消息")
async def report_message(
        message_id: uuid.UUID,
        payload: app_schema.MessageReportRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """举报消息中的违规内容

    支持选中反馈：
    - 用户选中回复中的某段文本，提交举报或反馈，标记不当内容
    - 上报数据包含：message_id + 文本起始偏移、结束偏移
    """
    # 验证消息存在
    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id
    )

    report_service = ReportService(db)
    result = report_service.submit_report(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        reported_by=end_user.id,
        report_type=payload.report_type,
        report_reason=payload.report_reason,
        text_start_offset=payload.text_start_offset,
        text_end_offset=payload.text_end_offset,
        selected_text=payload.selected_text,
    )

    return success(data=app_schema.MessageReportResponse(**result))


@router.post("/messages/{message_id}/regenerate", summary="重新生成回复")
async def regenerate_message(
        message_id: uuid.UUID,
        payload: app_schema.RegenerateRequest,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
        app_chat_service: Annotated[AppChatService, Depends(get_app_chat_service)] = None,
):
    """重新生成AI回复，支持多版本切换和流式输出

    核心逻辑：
    - 保持同一上下文、同一前置对话、同一用户提问
    - 不新增用户消息，只是复用当前会话截止到上一轮的 messages 上下文数组
    - 再次调用 LLM 生成新回答
    - 多版本历史保留、可切换回看

    支持流式输出：
    - stream=false: 返回完整的 JSON 响应
    - stream=true: 返回 SSE 流式事件
    """
    # 验证消息存在
    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id
    )

    # 获取配置
    agent_cfg = agent_config_4_app_release(release)

    if not (agent_cfg.model_parameters.get("deep_thinking", False) and payload.thinking):
        agent_cfg.model_parameters["deep_thinking"] = False

    # 获取存储类型
    storage_type = workspace_service.get_workspace_storage_type_without_auth(db=db, workspace_id=workspace_id)
    if storage_type is None:
        storage_type = 'neo4j'
    user_rag_memory_id = ''
    if storage_type == 'rag':
        if workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db, name="USER_RAG_MERORY", workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)

    if payload.stream:
        # 流式返回
        async def event_generator():
            async for event in app_chat_service.regenerate_stream(
                    message_id=message_id,
                    config=agent_cfg,
                    workspace_id=workspace_id,
                    user_id=str(end_user.id),
                    variables=payload.variables,
                    web_search=payload.web_search,
                    memory=payload.memory,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
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
    else:
        # 非流式返回
        result = await app_chat_service.regenerate(
            message_id=message_id,
            config=agent_cfg,
            workspace_id=workspace_id,
            user_id=str(end_user.id),
            variables=payload.variables,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

        return success(data=app_schema.RegenerateResponse(**result))


@router.post("/messages/{message_id}/switch-version/{version}", summary="切换消息版本")
async def switch_message_version(
        message_id: uuid.UUID,
        version: int,
        share_data: ShareTokenData = Depends(get_share_user_id),
        db: Session = Depends(get_db),
):
    """切换到指定版本的消息（仅限历史版本切换）"""
    # 验证消息存在
    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    # 通过 share_token 获取 workspace_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id
    conv_service = ConversationService(db)

    result = conv_service.switch_message_version(
        message_id=message_id,
        version=version,
        workspace_id=workspace_id,
    )

    return success(data=result)

