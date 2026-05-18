"""Memory 服务接口 - 基于 API Key 认证

复用 memory_agent_controller.py 中的内部接口，提供基于 API Key 认证的对外服务。

路由前缀: /memory
最终路径: /v1/memory/...
认证方式: API Key (@require_api_key)
"""

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import Response
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.api_key_utils import get_current_user_from_api_key, validate_end_user_in_workspace
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.quota_stub import check_end_user_quota
from app.core.response_utils import fail, success
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_agent_schema import Write_UserInput, UserInput

# 包装内部 controller
from app.controllers import memory_agent_controller

router = APIRouter(prefix="/memory", tags=["V1 - Memory API"])
logger = get_business_logger()


def _encode_result(result):
    """Encode result for JSON serialization, preserving Response objects as-is."""
    if isinstance(result, Response):
        return result
    return jsonable_encoder(result)


@router.get("")
async def get_memory_info():
    """获取记忆服务信息（占位）"""
    return success(data={}, msg="Memory API - Coming Soon")


@router.post("/write/sync")
@require_api_key(scopes=["memory"])
@check_end_user_quota
async def write_memory_sync(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """
    Write memory synchronously.

    Requires API Key with 'memory' scope.
    Input schema identical to internal POST /api/memory/writer_service (Write_UserInput).
    """
    body = await request.json()
    payload = Write_UserInput(**body)

    current_user = get_current_user_from_api_key(db, api_key_auth)
    validate_end_user_in_workspace(db, payload.end_user_id, api_key_auth.workspace_id)

    logger.info(f"V1 memory write (sync) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    result = await memory_agent_controller.write_server(
        user_input=payload,
        language_type=language_type,
        db=db,
        current_user=current_user,
    )
    return _encode_result(result)


@router.post("/read/sync")
@require_api_key(scopes=["memory"])
async def read_memory_sync(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """
    Read memory synchronously.

    Requires API Key with 'memory' scope.
    Input schema identical to internal POST /api/memory/read_service (UserInput).
    """
    body = await request.json()
    payload = UserInput(**body)

    current_user = get_current_user_from_api_key(db, api_key_auth)
    validate_end_user_in_workspace(db, payload.end_user_id, api_key_auth.workspace_id)

    logger.info(f"V1 memory read (sync) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    result = await memory_agent_controller.read_server(
        user_input=payload,
        db=db,
        current_user=current_user,
    )
    return _encode_result(result)


# NOTE: 内部 memory_agent_controller.write_server_async 当前已注释，V1 端同步注释，待恢复后同步启用
#
# @router.post("/write")
# @require_api_key(scopes=["memory"])
# async def write_memory_async(
#     request: Request,
#     api_key_auth: ApiKeyAuth = None,
#     db: Session = Depends(get_db),
#     message: str = Body(None, description="Request body"),
#     language_type: str = Header(default=None, alias="X-Language-Type"),
# ):
#     """
#     Write memory asynchronously (Celery task).
#
#     Requires API Key with 'memory' scope.
#     Returns task_id for polling via GET /write/status.
#     """
#     body = await request.json()
#     payload = Write_UserInput(**body)
#
#     current_user = get_current_user_from_api_key(db, api_key_auth)
#     validate_end_user_in_workspace(db, payload.end_user_id, api_key_auth.workspace_id)
#
#     logger.info(f"V1 memory write (async) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")
#
#     result = await memory_agent_controller.write_server_async(
#         user_input=payload,
#         language_type=language_type,
#         db=db,
#         current_user=current_user,
#     )
#     return _encode_result(result)


@router.post("/read")
@require_api_key(scopes=["memory"])
async def read_memory_async(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """
    Read memory asynchronously (Celery task).

    Requires API Key with 'memory' scope.
    Returns task_id for polling via GET /read/status.
    """
    body = await request.json()
    payload = UserInput(**body)

    current_user = get_current_user_from_api_key(db, api_key_auth)
    validate_end_user_in_workspace(db, payload.end_user_id, api_key_auth.workspace_id)

    logger.info(f"V1 memory read (async) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    result = await memory_agent_controller.read_server_async(
        user_input=payload,
        db=db,
        current_user=current_user,
    )
    return _encode_result(result)

# NOTE: /write/status 已禁用，因为其生产者（POST /write → write_server_async）当前已注释。
# 恢复时需同时启用 POST /write 和 GET /write/status 两个端点。
#
# @router.get("/write/status")
# @require_api_key(scopes=["memory"])
# async def get_write_task_status(
#     request: Request,
#     task_id: str = Query(..., description="Celery task ID"),
#     api_key_auth: ApiKeyAuth = None,
#     db: Session = Depends(get_db),
# ):
#     """查询异步写入任务状态"""
#     logger.info(f"V1 write task status - task_id: {task_id}")
#
#     from app.celery_task_scheduler import scheduler
#     result = scheduler.get_task_status(task_id)
#
#     return success(data=jsonable_encoder(result), msg="Task status retrieved")


@router.get("/read/status")
@require_api_key(scopes=["memory"])
async def get_read_task_status(
    request: Request,
    task_id: str = Query(..., description="Celery task ID"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Check the status of a memory read task.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 read task status - task_id: {task_id}")

    from app.services.task_service import get_task_memory_read_result
    result = get_task_memory_read_result(task_id)

    return success(data=jsonable_encoder(result), msg="Task status retrieved")