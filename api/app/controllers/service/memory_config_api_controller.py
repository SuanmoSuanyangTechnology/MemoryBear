"""Memory Config 服务接口 - 基于 API Key 认证"""

import uuid
from typing import Optional

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import memory_config_controller
from app.controllers import ontology_controller
from app.controllers.emotion_config_controller import EmotionConfigUpdate
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import get_current_user_snapshot_from_api_key_async
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context, get_db_context
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import (
    ConfigUpdateExtractedRequest,
    ConfigUpdateRequest,
    ConfigCreateRequest,
    ConfigUpdateForgettingRequest,
    EmotionConfigUpdateRequest,
    ReflectionConfigUpdateRequest,
)
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.schemas.memory_storage_schema import (
    ConfigUpdate,
    ConfigUpdateExtracted,
    ConfigParamsCreate,
)
from app.schemas.memory_storage_schema import ForgettingConfigUpdateRequest
from app.utils.config_utils import resolve_config_id_async

router = APIRouter(prefix="/memory_config", tags=["V1 - Memory Config API"])
logger = get_business_logger()


async def _verify_config_ownership_async(config_id: str, workspace_id: uuid.UUID, db: AsyncSession):
    """异步版本：验证 config 归属 workspace。

    Returns:
        None: 校验通过
        dict: fail() 响应（校验失败时直接 return 给客户端）
    """
    from app.core.response_utils import fail

    try:
        resolved_id = await resolve_config_id_async(config_id, db)
    except ValueError as e:
        return fail(BizCode.INVALID_PARAMETER, f"无效的配置ID: {e}")

    config = await MemoryConfigRepository(db).get_by_id_async(resolved_id)
    if not config:
        return fail(BizCode.MEMORY_CONFIG_NOT_FOUND, "配置不存在")
    if str(config.workspace_id) != str(workspace_id):
        return fail(BizCode.MEMORY_CONFIG_NOT_FOUND, "无权访问该配置")
    return None


# @router.get("/configs")
# @require_api_key(scopes=["memory"])
# async def list_memory_configs(
#     request: Request,
#     api_key_auth: ApiKeyAuth = None,
#     db: Session = Depends(get_db),
# ):
#     """
#     List all memory configs for the workspace.

#     Returns all available memory configurations associated with the authorized workspace.
#     """
#     logger.info(f"List configs request - workspace_id: {api_key_auth.workspace_id}")

#     memory_api_service = MemoryAPIService(db)

#     result = memory_api_service.list_memory_configs(
#         workspace_id=api_key_auth.workspace_id,
#     )

#     logger.info(f"Listed {result['total']} configs for workspace: {api_key_auth.workspace_id}")
#     return success(data=ListConfigsResponse(**result).model_dump(), msg="Configs listed successfully")

@router.get("/read_all_config")
@require_api_key_self_db(scopes=["memory"])
async def read_all_config(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
):
    """
    List all memory configs with full details (enhanced version).

    Returns complete config fields for the authorized workspace.
    No config_id ownership check needed — results are filtered by workspace.
    """
    logger.info(f"V1 get all configs (full) - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

        return await memory_config_controller.read_all_config(
            current_user=current_user,
        )


@router.get("/scenes/simple")
@require_api_key_self_db(scopes=["memory"])
async def get_ontology_scenes(
        request: Request,
        api_key_auth: ApiKeyAuth = None,
):
    """
    Get available ontology scenes for the workspace.

    Returns a simple list of scene_id and scene_name for dropdown selection.
    Used before creating a memory config to choose which ontology scene to associate.
    """
    logger.info(f"V1 get scenes - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Get extraction engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read extracted config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Get forgetting settings for a specific memory config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read forgetting config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Get emotion engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read emotion config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Get reflection engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read reflection config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Create a new memory config for the workspace.

    The config will be associated with the workspace of the API Key.
    config_name is required, other fields are optional.
    """
    body = await request.json()
    payload = ConfigCreateRequest(**body)

    logger.info(f"V1 create config - workspace: {api_key_auth.workspace_id}, config_name: {payload.config_name}")

    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    # 构造管理端 Schema，workspace_id 从 API Key 注入
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
    # create_config 有 @check_memory_engine_quota 装饰器，需要同步 db
    with get_db_context() as sync_db:
        result = await memory_config_controller.create_config(
            payload=mgmt_payload,
            current_user=current_user,
            db=sync_db,
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
    """
    Update memory config basic info (name, description, scene).

    Requires API Key with 'memory' scope
    Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = ConfigUpdateRequest(**body)

    logger.info(f"V1 update config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
     update memory config extraction engine config (models, thresholds, chunking, pruning, etc.).

     Requires API Key with 'memory' scope.
     Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = ConfigUpdateExtractedRequest(**body)

    logger.info(f"V1 update extracted config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        # 校验权限
        _ownership_error = await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
     update memory config forgetting settings (forgetting strategy, parameters, etc.).

     Requires API Key with 'memory' scope.
     Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = ConfigUpdateForgettingRequest(**body)

    logger.info(f"V1 update forgetting config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        # 校验权限
        _ownership_error = await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

        update_fields = payload.model_dump(exclude_unset=True)
        mgmt_payload = ForgettingConfigUpdateRequest(**update_fields)

        # 将返回数据中UUID序列化处理
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
    """
    Update emotion engine config (full update).

    All configuration fields are required.
    Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = EmotionConfigUpdateRequest(**body)

    logger.info(f"V1 update emotion config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Update reflection engine config (full update).

    All fields are required.
    Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = ReflectionConfigUpdateRequest(**body)

    logger.info(f"V1 update reflection config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(payload.config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

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
    """
    Delete a memory config.

    - Default configs cannot be deleted.
    - If end users are connected, returns a warning.

    Only configs belonging to the authorized workspace can be deleted.
    """
    logger.info(f"V1 delete config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as auth_db:
        _ownership_error = await _verify_config_ownership_async(config_id, api_key_auth.workspace_id, auth_db)
        if _ownership_error:
            return _ownership_error
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

        return await memory_config_controller.delete_config(
            config_id=config_id,
            current_user=current_user,
        )
