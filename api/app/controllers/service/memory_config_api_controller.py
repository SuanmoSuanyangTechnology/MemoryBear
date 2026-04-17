"""Memory Config 服务接口 - 基于 API Key 认证"""

from typing import Optional
import uuid

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.controllers import memory_storage_controller
from app.controllers import memory_forget_controller
from app.controllers import ontology_controller
from app.controllers import emotion_config_controller
from app.controllers import memory_reflection_controller
from app.schemas.memory_storage_schema import ForgettingConfigUpdateRequest
from app.controllers.emotion_config_controller import EmotionConfigUpdate
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import (
    ConfigUpdateExtractedRequest,
    ConfigUpdateRequest,
    ListConfigsResponse,
    ConfigCreateRequest,
    ConfigUpdateForgettingRequest,
    EmotionConfigUpdateRequest,
    ReflectionConfigUpdateRequest,
)
from app.schemas.memory_storage_schema import (
    ConfigUpdate, 
    ConfigUpdateExtracted,
    ConfigParamsCreate,
)
from app.services import api_key_service
from app.services.memory_api_service import MemoryAPIService
from app.utils.config_utils import resolve_config_id

router = APIRouter(prefix="/memory_config", tags=["V1 - Memory Config API"])
logger = get_business_logger()


def _get_current_user(api_key_auth: ApiKeyAuth, db: Session):
    """Build a current_user object from API key auth

    Args:
        api_key_auth: Validated API key auth info
        db: Database session

    Returns:
        User object with current_workspace_id set
    """
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id
    return current_user


def _verify_config_ownership(config_id:str, workspace_id:uuid.UUID, db:Session):
    """Verify that the config belongs to the workspace.
    
      Args: 
          config_id: The ID of the config to verify
          workspace_id: The workspace ID tocheck against
          db: Database session for querying
        Raises:
            BusinessException: If the config does not exist or does not belong to the workspace
    """
    try:
        resolved_id = resolve_config_id(config_id, db)
    except ValueError as e:
        raise BusinessException(
            message=f"Invalid config_id: {e}",
            code=BizCode.INVALID_PARAMETER,
        )
    config = MemoryConfigRepository.get_by_id(db, resolved_id)
    if not config or config.workspace_id != workspace_id:
        raise BusinessException(
            message="Config not found or access denied",
            code=BizCode.MEMORY_CONFIG_NOT_FOUND,
        )

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
@require_api_key(scopes=["memory"])
async def read_all_config(
    request:Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    List all memory configs with full details (enhanced version).

    Returns complete config fields for the authorized workspace.
    No config_id ownership check needed — results are filtered by workspace.
    """
    logger.info(f"V1 get all configs (full) - workspace: {api_key_auth.workspace_id}")

    current_user = _get_current_user(api_key_auth, db)

    return memory_storage_controller.read_all_config(
        current_user=current_user,
        db=db,
    )

@router.get("/scenes/simple")
@require_api_key(scopes=["memory"])
async def get_ontology_scenes(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get available ontology scenes for the workspace.

    Returns a simple list of scene_id and scene_name for dropdown selection.
    Used before creating a memory config to choose which ontology scene to associate.
    """
    logger.info(f"V1 get scenes - workspace: {api_key_auth.workspace_id}")

    current_user = _get_current_user(api_key_auth, db)

    return await ontology_controller.get_scenes_simple(
        db=db,
        current_user=current_user,
    )

@router.get("/read_config_extracted")
@require_api_key(scopes=["memory"])
async def read_config_extracted(
    request: Request,
    config_id: str = Query(..., description="config_id"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get extraction engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read extracted config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)

    return memory_storage_controller.read_config_extracted(
        config_id = config_id,
        current_user = current_user,
        db = db,
    )

@router.get("/read_config_forgetting")
@require_api_key(scopes=["memory"])
async def read_config_forgetting(
    request: Request,
    config_id: str = Query(..., description="config_id"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get forgetting settings for a specific memory config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read forgetting config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)

    result = await memory_forget_controller.read_forgetting_config(
        config_id = config_id,
        current_user = current_user,
        db = db,
    )
    return jsonable_encoder(result)



@router.get("/read_config_emotion")
@require_api_key(scopes=["memory"])
async def read_config_emotion(
    request: Request,
    config_id: str = Query(..., description="config_id"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get emotion engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read emotion config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)

    return jsonable_encoder(emotion_config_controller.get_emotion_config(
        config_id=config_id,
        db=db,
        current_user=current_user,
    ))

@router.get("/read_config_reflection")
@require_api_key(scopes=["memory"])
async def read_config_reflection(
    request: Request,
    config_id: str = Query(..., description="config_id"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get reflection engine config details for a specific config.

    Only configs belonging to the authorized workspace can be queried.
    """
    logger.info(f"V1 read reflection config - config_id: {config_id}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)

    return jsonable_encoder(await memory_reflection_controller.start_reflection_configs(
        config_id=config_id,
        current_user=current_user,
        db=db,
    ))


@router.post("/create_config")
@require_api_key(scopes=["memory"])
async def create_memory_config(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
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
    
    # 构造管理端 Schema，workspace_id 从 API Key 注入
    current_user = _get_current_user(api_key_auth, db)
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
    #将返回数据中UUID序列化处理
    result =memory_storage_controller.create_config(
        payload=mgmt_payload,
        current_user=current_user,
        db=db,
        x_language_type=x_language_type,
    )
    return jsonable_encoder(result)

@router.put("/update_config")
@require_api_key(scopes=["memory"])
async def update_memory_config(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
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

    _verify_config_ownership(payload.config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)
    mgmt_payload = ConfigUpdate(
        config_id = payload.config_id,
        config_name = payload.config_name,
        config_desc = payload.config_desc,
        scene_id = payload.scene_id,
    )

    return memory_storage_controller.update_config(
        payload = mgmt_payload,
        current_user = current_user,
        db = db,
    )

@router.put("/update_config_extracted")
@require_api_key(scopes=["memory"])
async def update_memory_config_extracted(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
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

   #校验权限
   _verify_config_ownership(payload.config_id, api_key_auth.workspace_id, db)

   current_user = _get_current_user(api_key_auth, db)
   update_fields = payload.model_dump(exclude_unset=True)
   mgmt_payload = ConfigUpdateExtracted(**update_fields)

   return memory_storage_controller.update_config_extracted(
        payload = mgmt_payload,
        current_user = current_user,
        db = db,
   )

@router.put("/update_config_forgetting")
@require_api_key(scopes=["memory"])
async def update_memory_config_forgetting(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
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

   #校验权限
   _verify_config_ownership(payload.config_id, api_key_auth.workspace_id, db)

   current_user = _get_current_user(api_key_auth, db)
   update_fields = payload.model_dump(exclude_unset=True)
   mgmt_payload = ForgettingConfigUpdateRequest(**update_fields)

   #将返回数据中UUID序列化处理
   result = await memory_forget_controller.update_forgetting_config(
        payload = mgmt_payload,
        current_user = current_user,
        db = db,
   )
   return jsonable_encoder(result)

@router.put("/update_config_emotion")
@require_api_key(scopes=["memory"])
async def update_config_emotion(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """
    Update emotion engine config (full update).

    All fields except emotion_model_id are required.
    Only configs belonging to the authorized workspace can be updated.
    """
    body = await request.json()
    payload = EmotionConfigUpdateRequest(**body)

    logger.info(f"V1 update emotion config - config_id: {payload.config_id}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(payload.config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = EmotionConfigUpdate(**update_fields)
    return jsonable_encoder(emotion_config_controller.update_emotion_config(
        config=mgmt_payload,
        db=db,
        current_user=current_user,
    ))

@router.put("/update_config_reflection")
@require_api_key(scopes=["memory"])
async def update_config_reflection(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
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

    _verify_config_ownership(payload.config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)
    update_fields = payload.model_dump(exclude_unset=True)
    mgmt_payload = Memory_Reflection(**update_fields)

    return jsonable_encoder(await memory_reflection_controller.save_reflection_config(
        request=mgmt_payload,
        current_user=current_user,
        db=db,
    ))

@router.delete("/delete_config")
@require_api_key(scopes=["memory"])
async def delete_memory_config(
    config_id: str,
    request: Request,
    force: bool = Query(False, description="是否强制删除（即使有终端用户正在使用）"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Delete a memory config.

    - Default configs cannot be deleted.
    - If end users are connected and force=False, returns a warning.
    - If force=True, clears end user references and deletes the config.

    Only configs belonging to the authorized workspace can be deleted.
    """
    logger.info(f"V1 delete config - config_id: {config_id}, force: {force}, workspace: {api_key_auth.workspace_id}")

    _verify_config_ownership(config_id, api_key_auth.workspace_id, db)

    current_user = _get_current_user(api_key_auth, db)

    return memory_storage_controller.delete_config(
        config_id=config_id,
        force=force,
        current_user=current_user,
        db=db,
    )
