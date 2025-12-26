"""App 服务接口 - 基于 API Key 认证"""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Body
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_app_or_workspace
from app.models.app_model import App
from app.models.app_model import AppType
from app.repositories import knowledge_repository
from app.repositories.end_user_repository import EndUserRepository
from app.schemas import AppChatRequest, conversation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.services import workspace_service
from app.services.app_chat_service import AppChatService, get_app_chat_service
from app.services.conversation_service import ConversationService, get_conversation_service
from app.utils.app_config_utils import dict_to_multi_agent_config, dict_to_workflow_config, agent_config_4_app_release
from app.services.app_service import get_app_service, AppService

router = APIRouter(prefix="/app", tags=["V1 - App API"])
logger = get_business_logger()


@router.get("")
async def list_apps():
    """列出可访问的应用（占位）"""
    return success(data=[], msg="App API - Coming Soon")

# /v1/apps/{resource_id}/chat

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
#         resource_id: 如果是应用的apikey传的是应用id; 如果是服务的apikey传的是工作空间id
#         message: 请求参数
#         request: 声明请求
#         api_key_auth: 包含验证后的API Key 信息
#         db: db_session
#     """
#     logger.info(f"API Key Auth: {api_key_auth}")
#     logger.info(f"Message: {message}")
#     return success(data={"received": True}, msg="消息已接收")


def _checkAppConfig(app: App):
    if app.type == AppType.AGENT:
        if not app.current_release.config:
            raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif app.type == AppType.MULTI_AGENT:
        if not app.current_release.config:
            raise BusinessException("Multi-Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif app.type == AppType.WORKFLOW:
        if not app.current_release.config:
            raise BusinessException("工作流应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    else:
        raise BusinessException("不支持的应用类型", BizCode.AGENT_CONFIG_MISSING)

@router.post("/chat")
@require_api_key(scopes=["app"])
async def chat(
    request:Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
    app_chat_service: Annotated[AppChatService, Depends(get_app_chat_service)] = None,
    app_service: Annotated[AppService, Depends(get_app_service)] = None,
    message: str = Body(..., description="聊天消息内容"),
):
    body = await request.json()
    payload = AppChatRequest(**body)

    other_id = payload.user_id
    app = app_service.get_app(api_key_auth.resource_id, api_key_auth.workspace_id)
    other_id = payload.user_id
    workspace_id = app.workspace_id
    end_user_repo = EndUserRepository(db)
    new_end_user = end_user_repo.get_or_create_end_user(
        app_id=app.id,
        other_id=other_id,
        original_user_id=other_id  # Save original user_id to other_id
    )
    end_user_id = str(new_end_user.id)
    web_search=True
    memory=True
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
    _checkAppConfig(app)

    # 获取或创建会话（提前验证）
    conversation = conversation_service.create_or_get_conversation(
        app_id=app.id,
        workspace_id=workspace_id,
        user_id=end_user_id,
        is_draft=False
    )

    if app_type == AppType.AGENT:

        print("="*50)
        print(app.current_release.default_model_config_id)
        agent_config = agent_config_4_app_release(app.current_release)
        print(agent_config.default_model_config_id)
        # 流式返回
        if payload.stream:
            async def event_generator():
                async for event in app_chat_service.agnet_chat_stream(
                    message=payload.message,
                    conversation_id=conversation.id,  # 使用已创建的会话 ID
                    user_id= end_user_id,  # 转换为字符串
                    variables=payload.variables,
                    web_search=web_search,
                    config=agent_config,
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

        # 非流式返回
        result = await app_chat_service.agnet_chat(
            message=payload.message,
            conversation_id=conversation.id,  # 使用已创建的会话 ID
            user_id=end_user_id,  # 转换为字符串
            variables=payload.variables,
            config= agent_config,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )
        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))
    elif app_type == AppType.MULTI_AGENT:
        # 多 Agent 流式返回
        config = dict_to_multi_agent_config(app.current_release.config,app.id)
        if payload.stream:
            async def event_generator():
                async for event in app_chat_service.multi_agent_chat_stream(

                    message=payload.message,
                    conversation_id=conversation.id,  # 使用已创建的会话 ID
                    user_id=end_user_id,  # 转换为字符串
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
    elif app_type == AppType.WORKFLOW:
        # 多 Agent 流式返回
        config = dict_to_workflow_config(app.current_release.config,app.id)
        if payload.stream:
            async def event_generator():
                async for event in app_chat_service.workflow_chat_stream(

                    message=payload.message,
                    conversation_id=conversation.id,  # 使用已创建的会话 ID
                    user_id=end_user_id,  # 转换为字符串
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

        #  非流式返回
        result = await app_chat_service.workflow_chat(

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
    else:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)
        pass
