"""Memory 服务接口 - 基于 API Key 认证

复用 memory_controller.py 中的内部接口，提供基于 API Key 认证的对外服务。

路由前缀: /memory
最终路径: /v1/memory/...
认证方式: API Key (@require_api_key)
"""

import asyncio

from fastapi import APIRouter, Body, Header, Request, Depends
from fastapi.encoders import jsonable_encoder
from starlette.responses import Response

# 包装内部 controller
from app.controllers import memory_controller
from app.core.api_key_auth import require_api_key, require_api_key_self_db
from app.core.api_key_utils import get_current_user_from_api_key, validate_end_user_in_workspace, \
    validate_end_user_in_workspace_async
from app.core.logging_config import get_business_logger
from app.core.memory.enums import Neo4jNodeType, SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.quota_stub import check_end_user_quota
from app.core.response_utils import success
from app.db import get_db_context, get_async_db_context, get_db
from app.dependencies import make_snapshot
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_agent_schema import Write_UserInput, InternalReadInput, ReadSyncInput
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

    Supports two modes:
    - Single user (backward-compatible): pass ``end_user_id`` (string).
      Returns ``{"answer": "...", "intermediate_outputs": [...]}``.
    - Multi user: pass ``end_user_ids`` (list of strings). Reads are executed
      concurrently via ``asyncio.gather``.
      Returns ``{"<end_user_id>": {"answer": "...", "intermediate_outputs": [...]}, ...}``.
    """
    body = await request.json()
    payload = ReadSyncInput(**body)

    if payload.end_user_ids:
        # ── Multi-user mode: concurrent reads ──
        end_user_ids = set(payload.end_user_ids)

        async with get_async_db_context() as db:
            for euid in end_user_ids:
                await validate_end_user_in_workspace_async(db, euid, api_key_auth.workspace_id)

        logger.info(
            f"V1 memory read (sync) - end_user_ids: {end_user_ids}, workspace: {api_key_auth.workspace_id}"
        )

        async def _read_for_user(euid: str) -> tuple[str, dict]:
            async with get_async_db_context() as db:
                config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(euid)
            service = await MemoryService.create(config_id, end_user_id=euid)
            memory = await service.read(
                payload.message, search_switch=SearchStrategy(payload.search_switch)
            )
            return euid, {
                "answer": memory.content,
                "intermediate_outputs": [_.model_dump() for _ in memory.memories],
            }

        results = await asyncio.gather(
            *[_read_for_user(euid) for euid in end_user_ids],
            return_exceptions=True
        )
        res = {}
        for data in results:
            if isinstance(data, Exception):
                pass
            res[data[0]] = data[1]

        return success(data={euid: data for data in results})

    # ── Single-user mode (backward-compatible) ──
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


@router.post("/read/internal")
@require_api_key_self_db(scopes=["memory"])
async def read_memory_internal(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        body_placeholder: str = Body(None, description="Placeholder - actual body parsed via request.json()"),
):
    """
    Read memory synchronously (internal).

    Requires API Key with 'memory' scope.
    Extended version of /read/sync that supports `include` (Neo4j node types filter)
    and `limit` (max memories to return).
    """
    body = await request.json()
    payload = InternalReadInput(**body)
    async with get_async_db_context() as db:
        await validate_end_user_in_workspace_async(db, payload.end_user_id, api_key_auth.workspace_id)
        config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(payload.end_user_id)
    logger.info(
        f"V1 memory read (internal) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    # Resolve include strings to Neo4jNodeType enum values
    includes = None
    if payload.includes:
        includes = [Neo4jNodeType(t) for t in payload.includes]

    service = await MemoryService.create(
        config_id,
        end_user_id=payload.end_user_id,
    )
    memory = await service.read(
        payload.message,
        search_switch=SearchStrategy(payload.search_switch),
        limit=payload.limit,
        includes=includes,
        skip_summary=payload.skip_summary
    )
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
        body_placeholder: str = Body(None, description="Placeholder - actual body parsed via request.json()"),
        language_type: str = Header(default=None, alias="X-Language-Type"),
        db: str = Depends(get_db),
):
    """
    Write memory asynchronously (Celery task).

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = Write_UserInput(**body)

    with get_db_context() as auth_db:
        current_user_orm = get_current_user_from_api_key(auth_db, api_key_auth)
        validate_end_user_in_workspace(auth_db, payload.end_user_id, api_key_auth.workspace_id)
        current_user = make_snapshot(current_user_orm, api_key_auth.workspace_id)

    logger.info(f"V1 memory write (async) - end_user_id: {payload.end_user_id}, workspace: {api_key_auth.workspace_id}")

    result = await memory_controller.write_server_async(
        user_input=payload,
        language_type=language_type,
        current_user=current_user,
    )
    return _encode_result(result)
