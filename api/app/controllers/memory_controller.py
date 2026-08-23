"""记忆读写服务接口 - 基于 JWT 认证

将同步读、异步写从 memory_agent_controller 迁出，统一收口本文件，
使对内 /api 路由与对外 /v1（memory_api_controller）保持一致。

路由前缀: /memory
认证方式: JWT Token
"""
import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.error_codes import BizCode, HTTP_MAPPING
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.memory_service import MemoryService
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import cur_workspace_access_guard, cur_workspace_access_guard_self_db, get_current_user_async, CurrentUserSnapshot
from app.repositories import knowledge_repository
from app.schemas.memory_agent_schema import StorageType, UserInput, Write_UserInput
from app.schemas.response_schema import ApiResponse
from app.services import workspace_service
from app.services.memory_agent_service import MemoryAgentService
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_validation_service import MemoryValidationService

DEFAULT_STORAGE_TYPE = "neo4j"
USER_RAG_MEMORY_KNOWLEDGE_NAME = "USER_RAG_MERORY"  # 注：原拼写保留，历史遗留

api_logger = get_api_logger()

memory_agent_service = MemoryAgentService()

router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.post("/write", response_model=ApiResponse)
@cur_workspace_access_guard()
async def write_server_async(
        user_input: Write_UserInput,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async)
):
    """
    Async write service endpoint - enqueues write processing to Celery

    Args:
        user_input: Write request containing message and end_user_id
        language_type: 语言类型 ("zh" 中文, "en" 英文)，通过 X-Language-Type Header 传递

    Returns:
        Task ID for tracking async operation
    """
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    storage_type = None
    user_rag_memory_id = ''
    workspace_id = current_user.current_workspace_id
    async with get_async_db_context() as db:
        config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(user_input.end_user_id)
        api_logger.info(
            f"Async write service: workspace_id={workspace_id}, config_id={config_id}, language_type={language}")

        # 获取 storage_type，如果为 None 则使用默认值
        storage_type = await workspace_service.get_workspace_storage_type_async(
            db=db,
            workspace_id=workspace_id,
            user=current_user
        )
        if storage_type is None: storage_type = DEFAULT_STORAGE_TYPE
        if workspace_id:

            knowledge = await knowledge_repository.get_knowledge_by_name_async(
                db=db,
                name=USER_RAG_MEMORY_KNOWLEDGE_NAME,
                workspace_id=workspace_id
            )
            if knowledge: user_rag_memory_id = str(knowledge.id)
        api_logger.info(f"Async write: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")

    try:
        # ── RAG 路径：保持不变，直接拼接文本写向量库 ──
        if storage_type and storage_type.lower() == StorageType.RAG.value:
            messages_list = memory_agent_service.get_messages_list(user_input)
            await MemoryService.write_messages_to_rag(
                messages=messages_list,
                end_user_id=user_input.end_user_id,
                user_rag_memory_id=user_rag_memory_id,
            )
            api_logger.info(f"RAG write completed for end_user={user_input.end_user_id}")
            return success(data={}, msg="RAG 写入完成")

        # ── Neo4j 路径：通过 dispatcher 写入 ──
        workspace_id_str = str(current_user.current_workspace_id) if current_user.current_workspace_id else ""
        messages_list = memory_agent_service.get_messages_list(user_input)

        task_ids = await MemoryService.dispatch_api_service_async(
            messages=messages_list,
            end_user_id=user_input.end_user_id,
            config_id=config_id,
            workspace_id=workspace_id_str,
            language=language,
        )

        api_logger.info(
            f"Write tasks queued: {len(task_ids)} tasks, end_user={user_input.end_user_id}"
        )

        return success(data={"task_ids": task_ids}, msg=f"已提交 {len(task_ids)} 个写入任务")
    except Exception as e:
        api_logger.error(f"Async write operation failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "写入失败", str(e))


@router.post("/read/sync", response_model=None)
@cur_workspace_access_guard_self_db()
async def read_server(
        user_input: UserInput,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async)
) -> StreamingResponse | JSONResponse:
    """
    记忆验证读取接口：请求保持不变，响应改为 SSE。

    search_switch values:
    - "0": Deep
    - "1": Normal
    - "2": Quick
    - "5": Express
    """
    request_id = str(uuid.uuid4())
    try:
        # 开流前完成参数和服务初始化，失败时仍返回普通 JSON 错误。
        validation_service = await MemoryValidationService.create(
            user_input,
            request_id=request_id,
        )
    except Exception as error:
        api_logger.error(
            "Unable to initialize memory read: request_id=%s, end_user=%s, error=%s",
            request_id,
            user_input.end_user_id,
            str(error),
            exc_info=True,
        )
        error_data = fail(BizCode.MEMORY_READ_FAILED, "回复对话消息失败")
        return JSONResponse(
            status_code=HTTP_MAPPING[BizCode.MEMORY_READ_FAILED],
            content=jsonable_encoder(error_data),
        )

    api_logger.info(
        "Read service stream started: request_id=%s, group=%s, backend=%s, session_id=%s",
        request_id,
        user_input.end_user_id,
        DEFAULT_STORAGE_TYPE,
        validation_service.session_id,
    )
    return StreamingResponse(
        validation_service.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
