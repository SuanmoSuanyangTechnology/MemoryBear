"""Memory Config 服务接口 - 基于 API Key 认证（纯异步版本）

所有 /v1/memory_config/* 端点统一走 ``@require_api_key_self_db`` +
``get_current_user_from_api_key_async``；权限校验（config 归属）走 async
Repository；实际业务委托到 P2 已改为纯异步的 memory_config_controller
（无 db 参数）。
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.encoders import jsonable_encoder

from app.controllers import memory_config_controller, ontology_controller
from app.controllers.emotion_config_controller import EmotionConfigUpdate
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import get_current_user_from_api_key_async
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import (
    ConfigCreateRequest,
    ConfigUpdateExtractedRequest,
    ConfigUpdateForgettingRequest,
    ConfigUpdateRequest,
    EmotionConfigUpdateRequest,
    ReflectionConfigUpdateRequest,
)
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.schemas.memory_storage_schema import (
    ConfigParamsCreate,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ForgettingConfigUpdateRequest,
)
from app.schemas.ontology_schemas import SceneSimpleListResponse
from app.utils.config_utils import resolve_config_id_async

router = APIRouter(prefix="/memory_config", tags=["V1 - Memory Config API"])
logger = get_business_logger()


async def _resolve_current_user(api_key_auth: ApiKeyAuth) -> CurrentUserSnapshot:
    """异步版本：从 API Key 反查 creator，转为 CurrentUserSnapshot 快照。"""
    async with get_async_db_context() as db:
        user = await get_current_user_from_api_key_async(db, api_key_auth)
        return CurrentUserSnapshot(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            current_workspace_id=getattr(user, "current_workspace_id", api_key_auth.workspace_id),
            tenant_id=user.tenant_id,
            preferred_language=getattr(user, "preferred_language", None),
        )


async def _verify_config_ownership_async(config_id: str, workspace_id: uuid.UUID) -> None:
    """异步校验 config 归属工作空间（单 async session）。

    Raises:
        BusinessException: config_id 非法或不属于该 workspace
    """
    async with get_async_db_context() as db:
        try:
            resolved_id = await resolve_config_id_async(config_id, db)
        except ValueError as e:
            raise BusinessException(
                message=f"Invalid config_id: {e}",
                code=BizCode.INVALID_PARAMETER,
            )

        config = await MemoryConfigRepository(db).get_by_id_async(resolved_id)
        if not config or config.workspace_id != workspace_id:
            raise BusinessException(
                message="Config not found or access denied",
                code=BizCode.MEMORY_CONFIG_NOT_FOUND,
            )


@router.get("/read_all_config")
@require_api_key_self_db(scopes=["memory"])
async def read_all_config(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
):
    """List all memory configs with full details (workspace-scoped)."""
    logger.info(f"V1 get all configs (full) - workspace: {api_key_auth.workspace_id}")

    current_user = await _resolve_current_user(api_key_auth)
    return await memory_config_controller.read_all_config(current_user=current_user)


@router.get("/scenes/simple", response_model=SceneSimpleListResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_ontology_scenes(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
):
    """Get available ontology scenes."""
    logger.info(f"V1 get scenes - workspace: {api_key_auth.workspace_id}")

    current_user = await _resolve_current_user(api_key_auth)

    return await ontology_controller.get_scenes_simple(
        current_user=current_user,
    )


@router.get("/read_config_extracted")
@require_api_key_self_db(scopes=["memory"])
async def read_config_extracted(
        request: Request,
        config_id: str = Query(..., description="config_id"),
        api_key_auth: ApiKeyAuth = None,
):
    """Get extraction engine config details for a specific config."""
    logger.info(f"V1 read extracted config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(config_id, api_key_auth.workspace_id)
    current_user = await _resolve_current_user(api_key_auth)
    return await memory_config_controller.read_config_extracted(
        config_id=config_id,
        current_user=current_user,
    )


@router.get("/read_config_forgetting")
@require_api_key_self_db(scopes=["memory"])
async def read_config_forgetting(
        request: Request,
        config_id: str = Query(..., description="config_id"),
        api_key_auth: ApiKeyAuth = None,
):
    """Get forgetting settings for a specific memory config."""
    logger.info(f"V1 read forgetting config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(config_id, api_key_auth.workspace_id)
    current_user = await _resolve_current_user(api_key_auth)
    result = await memory_config_controller.read_forgetting_config(
        config_id=config_id,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.get("/read_config_emotion")
@require_api_key_self_db(scopes=["memory"])
async def read_config_emotion(
        request: Request,
        config_id: str = Query(..., description="config_id"),
        api_key_auth: ApiKeyAuth = None,
):
    """Get emotion engine config details for a specific config."""
    logger.info(f"V1 read emotion config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(config_id, api_key_auth.workspace_id)
    current_user = await _resolve_current_user(api_key_auth)
    return jsonable_encoder(await memory_config_controller.get_emotion_config(
        config_id=config_id,
        current_user=current_user,
    ))


@router.get("/read_config_reflection")
@require_api_key_self_db(scopes=["memory"])
async def read_config_reflection(
        request: Request,
        config_id: str = Query(..., description="config_id"),
        api_key_auth: ApiKeyAuth = None,
):
    """Get reflection engine config details for a specific config."""
    logger.info(f"V1 read reflection config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(config_id, api_key_auth.workspace_id)
    current_user = await _resolve_current_user(api_key_auth)
    return jsonable_encoder(await memory_config_controller.start_reflection_configs(
        config_id=config_id,
        current_user=current_user,
    ))


@router.post("/create_config")
@require_api_key_self_db(scopes=["memory"])
async def create_memory_config(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
        x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
):
    """Create a new memory config for the workspace."""
    body = await request.json()
    payload = ConfigCreateRequest(**body)

    logger.info(f"V1 create config - workspace: {api_key_auth.workspace_id}, config_name: {payload.config_name}")

    current_user = await _resolve_current_user(api_key_auth)
    mgmt_payload = ConfigParamsCreate(
        config_name=payload.config_name,
        config_desc=payload.config_desc or "",
        scene_id=payload.scene_id,
        llm_id=payload.llm_id,
        embedding_id=payload.embedding_id,
        rerank_id=payload.rerank_id,
        reflection_model_id=payload.reflection_model_id,
        emotion_model_id=payload.emotion_model_id,
    )
    result = await memory_config_controller.create_config(
        payload=mgmt_payload,
        current_user=current_user,
        x_language_type=x_language_type,
    )
    return jsonable_encoder(result)


@router.put("/update_config")
@require_api_key_self_db(scopes=["memory"])
async def update_memory_config(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
):
    """Update memory config basic info (name, description, scene)."""
    body = await request.json()
    payload = ConfigUpdateRequest(**body)

    logger.info(f"V1 update config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    mgmt_payload = ConfigUpdate(
        config_id=payload.config_id,
        config_name=payload.config_name,
        config_desc=payload.config_desc,
        scene_id=payload.scene_id,
    )
    return await memory_config_controller.update_config(
        payload=mgmt_payload,
        current_user=current_user,
    )


@router.put("/update_config_extracted")
@require_api_key_self_db(scopes=["memory"])
async def update_memory_config_extracted(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
):
    """Update memory config extraction engine config (models, thresholds, chunking, pruning, etc.)."""
    body = await request.json()
    payload = ConfigUpdateExtractedRequest(**body)

    logger.info(f"V1 update extracted config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = ConfigUpdateExtracted(**update_fields)
    return await memory_config_controller.update_config_extracted(
        payload=mgmt_payload,
        current_user=current_user,
    )


@router.put("/update_config_forgetting")
@require_api_key_self_db(scopes=["memory"])
async def update_memory_config_forgetting(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
):
    """Update memory config forgetting settings (strategy, parameters)."""
    body = await request.json()
    payload = ConfigUpdateForgettingRequest(**body)

    logger.info(f"V1 update forgetting config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = ForgettingConfigUpdateRequest(**update_fields)

    result = await memory_config_controller.update_forgetting_config(
        payload=mgmt_payload,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.put("/update_config_emotion")
@require_api_key_self_db(scopes=["memory"])
async def update_config_emotion(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
):
    """Update emotion engine config (full update)."""
    body = await request.json()
    payload = EmotionConfigUpdateRequest(**body)

    logger.info(f"V1 update emotion config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = EmotionConfigUpdate(**update_fields)
    return jsonable_encoder(await memory_config_controller.update_emotion_config(
        config=mgmt_payload,
        current_user=current_user,
    ))


@router.put("/update_config_reflection")
@require_api_key_self_db(scopes=["memory"])
async def update_config_reflection(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
        message: str = Body(None, description="Request body"),
):
    """Update reflection engine config (full update)."""
    body = await request.json()
    payload = ReflectionConfigUpdateRequest(**body)

    logger.info(f"V1 update reflection config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = Memory_Reflection(**update_fields)
    return jsonable_encoder(await memory_config_controller.save_reflection_config(
        request=mgmt_payload,
        current_user=current_user,
    ))


@router.delete("/delete_config")
@require_api_key_self_db(scopes=["memory"])
async def delete_memory_config(
        config_id: str,
        request: Request,
        api_key_auth: ApiKeyAuth = None,
):
    """Delete a memory config (workspace-scoped)."""
    logger.info(f"V1 delete config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")
    await _verify_config_ownership_async(config_id, api_key_auth.workspace_id)

    current_user = await _resolve_current_user(api_key_auth)
    return await memory_config_controller.delete_config(
        config_id=config_id,
        current_user=current_user,
    )
