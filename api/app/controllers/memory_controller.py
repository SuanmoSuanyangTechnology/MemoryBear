"""记忆读写服务接口 - 基于 JWT 认证

将同步读、异步写从 memory_agent_controller 迁出，统一收口本文件，
使对内 /api 路由与对外 /v1（memory_api_controller）保持一致。

路由前缀: /memory
认证方式: JWT Token
"""
from app.core.memory.channel_policy import require_neo4j_memory
import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.memory_service import MemoryService
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import cur_workspace_access_guard, cur_workspace_access_guard_self_db, get_current_user_async, CurrentUserSnapshot
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.memory_agent_schema import StorageType, UserInput, Write_UserInput
from app.schemas.memory_config_schema import ModelInactiveError, ModelNotFoundError
from app.schemas.response_schema import ApiResponse
from app.services import workspace_service
from app.services.end_user_service import EndUserService
from app.services.memory_agent_service import MemoryAgentService
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_validation_service import MemoryValidationService
from app.utils.sse_utils import format_sse_message

DEFAULT_STORAGE_TYPE = "neo4j"

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
    workspace_id = current_user.current_workspace_id
    # 跨渠道身份确认后的最终写入落点，默认与请求一致；命中归并时被覆盖为 target
    effective_end_user_id = user_input.end_user_id
    identity_data = None
    async with get_async_db_context() as db:
        storage_type = await workspace_service.get_workspace_storage_type_async(
            db=db, workspace_id=workspace_id, user=current_user
        )
        require_neo4j_memory(storage_type)
        # ── 跨渠道身份确认（仅在带标识时执行；不带/空串/纯空白则完全不碰身份字段）──
        if user_input.identity_features and user_input.identity_features.strip():
            clean_features = user_input.identity_features.strip()
            end_user = await EndUserRepository(db).get_end_user_by_id_async(
                uuid.UUID(user_input.end_user_id)
            )
            if end_user is None:
                return fail(BizCode.USER_NOT_FOUND, "终端用户不存在", "end_user not found")
            if str(end_user.workspace_id) != str(workspace_id):
                return fail(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "workspace mismatch")
            # 快路径条件同时校验 status：只比标识时，若标识已落库而 status 不是
            # confirmed（历史数据、S4 写入的 expired、或上一次归并中途失败留下的
            # 脏状态），身份确认会被永久跳过、无法自愈。
            if (end_user.identity_features or "") != clean_features \
                    or end_user.identity_status != "confirmed":
                # 标识与库中现值不同（或状态未确认）：进入身份确认（可能加锁 + 归并）
                final_id, identity_status, merged = await EndUserService(db).confirm_identity(
                    workspace_id=workspace_id,
                    current_end_user=end_user,
                    identity_features=user_input.identity_features,
                )
                effective_end_user_id = str(final_id)
                identity_data = {
                    "end_user_id": effective_end_user_id,
                    "identity_status": identity_status,
                    "merged": merged,
                }
            else:
                # 幂等快路径：标识没变且已是 confirmed，不加锁、不归并，仅回传当前身份状态
                identity_data = {
                    "end_user_id": str(end_user.id),
                    "identity_status": end_user.identity_status,
                    "merged": False,
                }

        config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(effective_end_user_id)
        api_logger.info(
            f"Async write service: workspace_id={workspace_id}, config_id={config_id}, language_type={language}")


    try:
        # ── Neo4j 路径：通过 dispatcher 写入 ──
        workspace_id_str = str(current_user.current_workspace_id) if current_user.current_workspace_id else ""
        messages_list = memory_agent_service.get_messages_list(user_input)

        task_ids = await MemoryService.dispatch_api_service_async(
            messages=messages_list,
            end_user_id=effective_end_user_id,
            config_id=config_id,
            workspace_id=workspace_id_str,
            language=language,
        )

        api_logger.info(
            f"Write tasks queued: {len(task_ids)} tasks, end_user={effective_end_user_id}"
        )

        data = {"task_ids": task_ids}
        if identity_data:
            data.update(identity_data)
        return success(data=data, msg=f"已提交 {len(task_ids)} 个写入任务")
    except Exception as e:
        api_logger.error(f"Async write operation failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "写入失败", str(e))


@router.post("/read/sync", response_model=None)
@cur_workspace_access_guard_self_db()
async def read_server(
        user_input: UserInput,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async)
) -> StreamingResponse:
    """
    记忆验证读取接口：请求保持不变，响应改为 SSE。

    search_switch values:
    - "0": Deep
    - "1": Normal
    - "2": Quick
    - "5": Express
    """
    request_id = str(uuid.uuid4())

    def _error_stream(message: str) -> StreamingResponse:
        async def _stream():
            yield format_sse_message("error", {
                "request_id": request_id,
                "code": BizCode.MEMORY_READ_FAILED,
                "message": message,
            })

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        # 开流前完成参数和服务初始化，失败时统一返回 SSE error 事件。
        validation_service = await MemoryValidationService.create(
            user_input,
            request_id=request_id,
        )
    except (ModelInactiveError, ModelNotFoundError) as e:
        api_logger.error(
            "Unable to initialize memory read: request_id=%s, end_user=%s, error=%s",
            request_id,
            user_input.end_user_id,
            str(e),
            exc_info=True,
        )
        return _error_stream(f"{e.context.get('model_type')}模型不可用")
    except Exception as error:
        api_logger.error(
            "Unable to initialize memory read: request_id=%s, end_user=%s, error=%s",
            request_id,
            user_input.end_user_id,
            str(error),
            exc_info=True,
        )
        return _error_stream("回复对话消息失败")

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
