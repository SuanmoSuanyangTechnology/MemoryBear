"""Memory 服务接口 - 基于 API Key 认证"""

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import (
    MemoryReadRequest,
    MemoryReadResponse,
    MemoryReadSyncResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    MemoryWriteSyncResponse,
)
from app.services.memory_api_service import MemoryAPIService

router = APIRouter(prefix="/memory", tags=["V1 - Memory API"])
logger = get_business_logger()


def _sanitize_task_result(result: dict) -> dict:
    """Make Celery task result JSON-serializable.

    Converts UUID and other non-serializable values to strings.

    Args:
        result: Raw task result dict from task_service

    Returns:
        JSON-safe dict
    """
    import uuid as _uuid
    from datetime import datetime

    def _convert(obj):
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        if isinstance(obj, _uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    return _convert(result)


@router.get("")
async def get_memory_info():
    """获取记忆服务信息（占位）"""
    return success(data={}, msg="Memory API - Coming Soon")


@router.post("/write")
@require_api_key(scopes=["memory"])
async def write_memory(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="Message content"),
):
    """
    Submit a memory write task.

    Validates the end user, then dispatches the write to a Celery background task
    with per-user fair locking. Returns a task_id for status polling.
    """
    body = await request.json()
    payload = MemoryWriteRequest(**body)
    logger.info(f"Memory write request - end_user_id: {payload.end_user_id}, workspace_id: {api_key_auth.workspace_id}")

    memory_api_service = MemoryAPIService(db)

    result = memory_api_service.write_memory(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )

    logger.info(f"Memory write task submitted: task_id={result['task_id']}, end_user_id: {payload.end_user_id}")
    return success(data=MemoryWriteResponse(**result).model_dump(), msg="Memory write task submitted")


@router.get("/write/status")
@require_api_key(scopes=["memory"])
async def get_write_task_status(
    request: Request,
    task_id: str = Query(..., description="Celery task ID"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Check the status of a memory write task.

    Returns the current status and result (if completed) of a previously submitted write task.
    """
    logger.info(f"Write task status check - task_id: {task_id}")

    from app.services.task_service import get_task_memory_write_result
    result = get_task_memory_write_result(task_id)

    return success(data=_sanitize_task_result(result), msg="Task status retrieved")


@router.post("/read")
@require_api_key(scopes=["memory"])
async def read_memory(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="Query message"),
):
    """
    Submit a memory read task.

    Validates the end user, then dispatches the read to a Celery background task.
    Returns a task_id for status polling.
    """
    body = await request.json()
    payload = MemoryReadRequest(**body)
    logger.info(f"Memory read request - end_user_id: {payload.end_user_id}")

    memory_api_service = MemoryAPIService(db)

    result = memory_api_service.read_memory(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        search_switch=payload.search_switch,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )

    logger.info(f"Memory read task submitted: task_id={result['task_id']}, end_user_id: {payload.end_user_id}")
    return success(data=MemoryReadResponse(**result).model_dump(), msg="Memory read task submitted")


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

    Returns the current status and result (if completed) of a previously submitted read task.
    """
    logger.info(f"Read task status check - task_id: {task_id}")

    from app.services.task_service import get_task_memory_read_result
    result = get_task_memory_read_result(task_id)

    return success(data=_sanitize_task_result(result), msg="Task status retrieved")


@router.post("/write/sync")
@require_api_key(scopes=["memory"])
async def write_memory_sync(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="Message content"),
):
    """
    Write memory synchronously.

    Blocks until the write completes and returns the result directly.
    For async processing with task polling, use /write instead.
    """
    body = await request.json()
    payload = MemoryWriteRequest(**body)
    logger.info(f"Memory write (sync) request - end_user_id: {payload.end_user_id}")

    memory_api_service = MemoryAPIService(db)

    result = await memory_api_service.write_memory_sync(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )

    logger.info(f"Memory write (sync) successful for end_user: {payload.end_user_id}")
    return success(data=MemoryWriteSyncResponse(**result).model_dump(), msg="Memory written successfully")


@router.post("/read/sync")
@require_api_key(scopes=["memory"])
async def read_memory_sync(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="Query message"),
):
    """
    Read memory synchronously.

    Blocks until the read completes and returns the answer directly.
    For async processing with task polling, use /read instead.
    """
    body = await request.json()
    payload = MemoryReadRequest(**body)
    logger.info(f"Memory read (sync) request - end_user_id: {payload.end_user_id}")

    memory_api_service = MemoryAPIService(db)

    result = await memory_api_service.read_memory_sync(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        search_switch=payload.search_switch,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )

    logger.info(f"Memory read (sync) successful for end_user: {payload.end_user_id}")
    return success(data=MemoryReadSyncResponse(**result).model_dump(), msg="Memory read successfully")
