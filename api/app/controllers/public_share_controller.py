import hashlib
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_share_user_id, ShareTokenData
from app.repositories import knowledge_repository
from app.schemas import release_share_schema, conversation_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services import workspace_service
from app.services.auth_service import create_access_token
from app.services.conversation_service import ConversationService
from app.services.release_share_service import ReleaseShareService
from app.services.shared_chat_service import SharedChatService

router = APIRouter(prefix="/public/share", tags=["Public Share"])
logger = get_business_logger()


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
    share, release = service._get_release_by_share_token(share_data.share_token, password)
    from app.repositories.end_user_repository import EndUserRepository
    end_user_repo = EndUserRepository(db)
    new_end_user = end_user_repo.get_or_create_end_user(
            app_id=share.app_id,
            other_id=other_id
        )
    logger.debug(new_end_user.id)
    service = SharedChatService(db)
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

    # 获取消息
    conv_service = ConversationService(db)
    messages = conv_service.get_messages(conversation_id)

    # 构建响应
    conv_dict = conversation_schema.Conversation.model_validate(conversation).model_dump()
    conv_dict["messages"] = [
        conversation_schema.Message.model_validate(m) for m in messages
    ]

    return success(data=conv_dict)


# ---------- 聊天接口 ----------

@router.post(
    "/chat",
    summary="发送消息（支持流式和非流式）"
)
async def chat(
    payload: conversation_schema.ChatRequest,
    share_data: ShareTokenData = Depends(get_share_user_id),
    db: Session = Depends(get_db)
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
    from app.models.app_model import AppType
    try:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        from app.services.app_service import AppService
        # 验证分享链接和密码
        share, release = service._get_release_by_share_token(share_token, password)

        # # Create end_user_id by concatenating app_id with user_id
        # end_user_id = f"{share.app_id}_{user_id}"

        # Store end_user_id in database with original user_id
        from app.repositories.end_user_repository import EndUserRepository
        end_user_repo = EndUserRepository(db)
        new_end_user = end_user_repo.get_or_create_end_user(
            app_id=share.app_id,
            other_id=other_id,
            original_user_id=user_id  # Save original user_id to other_id
        )


        appid=share.app_id
        """获取存储类型和工作空间的ID"""

        # 直接通过 SQLAlchemy 查询 app
        from app.models.app_model import App
        app = db.query(App).filter(App.id == appid).first()
        if not app:
            raise BusinessException("应用不存在", BizCode.APP_NOT_FOUND)

        workspace_id = app.workspace_id

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
        if app_type == "agent":
            # Agent 类型：验证模型配置
            model_config_id = release.default_model_config_id
            if not model_config_id:
                raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
        elif app_type == "multi_agent":
            # Multi-Agent 类型：验证多 Agent 配置
            config = release.config or {}
            if not config.get("sub_agents"):
                raise BusinessException("多 Agent 应用未配置子 Agent", BizCode.AGENT_CONFIG_MISSING)
        else:
            raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)

        # 获取或创建会话（提前验证）
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
                "conversation_id": str(conversation.id),
                "stream": payload.stream
            }
        )

    except Exception as e:
        # 验证失败，直接抛出异常（会被 FastAPI 的异常处理器捕获）
        logger.error(f"参数验证失败: {str(e)}")
        raise

    if app_type == AppType.AGENT:
        # 流式返回
        if payload.stream:
            async def event_generator():
                async for event in service.chat_stream(
                    share_token=share_token,
                    message=payload.message,
                    conversation_id=conversation.id,  # 使用已创建的会话 ID
                    user_id=str(new_end_user.id),  # 转换为字符串
                    variables=payload.variables,
                    password=password,
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

        # 非流式返回
        result = await service.chat(
            share_token=share_token,
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=str(new_end_user.id),  # 转换为字符串
            variables=payload.variables,
            password=password,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )
        return success(data=conversation_schema.ChatResponse(**result))
    elif app_type == AppType.MULTI_AGENT:
        # 多 Agent 流式返回
        if payload.stream:
            async def event_generator():
                async for event in service.multi_agent_chat_stream(
                    share_token=share_token,
                    message=payload.message,
                    conversation_id=conversation.id,  # 使用已创建的会话 ID
                    user_id=str(new_end_user.id),  # 转换为字符串
                    variables=payload.variables,
                    password=password,
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
        result = await service.multi_agent_chat(
            share_token=share_token,
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=str(new_end_user.id),  # 转换为字符串
            variables=payload.variables,
            password=password,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )

        return success(data=conversation_schema.ChatResponse(**result))
    else:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)
        pass
