"""Memory 服务接口 - 基于 API Key 认证

复用 memory_controller.py 中的内部接口，提供基于 API Key 认证的对外服务。

路由前缀: /memory
最终路径: /v1/memory/...
认证方式: API Key (@require_api_key)
"""

from fastapi import APIRouter, Body, Depends, Header, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from starlette.responses import Response

# 包装内部 controller
from app.controllers import memory_controller
from app.core.api_key_auth import require_api_key, require_api_key_self_db
from app.core.api_key_utils import get_current_user_from_api_key, validate_end_user_in_workspace, \
    validate_end_user_in_workspace_async
from app.core.logging_config import get_business_logger
from app.core.memory.enums import SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.quota_stub import check_end_user_quota
from app.core.response_utils import success
from app.db import get_db, get_async_db_context
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_agent_schema import Write_UserInput, UserInput
from app.services.memory_config_service import MemoryConfigService

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


@router.post("/read/sync")
@require_api_key_self_db(scopes=["memory"])
async def read_memory_sync(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        body_placeholder: str = Body(None, description="Placeholder - actual body parsed via request.json()"),
):
    """
    Read memory synchronously.

    Requires API Key with 'memory' scope.
    Input schema identical to internal POST /api/memory/read/sync (UserInput).
    """
    body = await request.json()
    payload = UserInput(**body)
    async with get_async_db_context() as db:
        await validate_end_user_in_workspace_async(db, payload.end_user_id, api_key_auth.workspace_id)
        config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(payload.end_user_id)
    logger.info(f"V1 memory read (sync) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")
    service = await MemoryService.create(
        config_id,
        end_user_id=payload.end_user_id,
    )
    memory = await service.read(payload.message, search_switch=SearchStrategy(payload.search_switch))
    return success(data={
        "answer": memory.content,
        "intermediate_outputs": [_.model_dump() for _ in memory.memories]
    })


@router.post("/write")
@require_api_key(scopes=["memory"])
@check_end_user_quota
async def write_memory_async(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        body_placeholder: str = Body(None, description="Placeholder - actual body parsed via request.json()"),
        language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """
    Write memory asynchronously (Celery task).

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = Write_UserInput(**body)

    current_user = get_current_user_from_api_key(db, api_key_auth)
    validate_end_user_in_workspace(db, payload.end_user_id, api_key_auth.workspace_id)

    logger.info(f"V1 memory write (async) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    result = await memory_controller.write_server_async(
        user_input=payload,
        language_type=language_type,
        db=db,
        current_user=current_user,
    )
    return _encode_result(result)
