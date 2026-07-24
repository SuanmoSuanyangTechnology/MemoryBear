"""Ontology 服务接口 - 基于 API Key 认证（P4 async 迁移版本）

包装 ontology_controller.py 中的内部接口，提供基于 API Key 认证的对外服务。

所有端点统一使用 ``@require_api_key_self_db``：装饰器内部使用 async DB
完成 API Key 校验和限流；``current_user`` 通过
``get_current_user_from_api_key_async`` 异步构造。业务处理仍委托到 sync
Session 版本的 ontology_controller（内部 service / repo 短期保持 sync，
避免引入无消费者的 async 方法）。

路由前缀: /memory/ontology
最终路径: /v1/memory/ontology/...
认证方式: API Key（``require_api_key_self_db``）
"""

from typing import Optional

from fastapi import APIRouter, Body, File, Form, Header, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from starlette.responses import Response

from app.controllers import ontology_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import get_current_user_from_api_key_async
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.ontology_schemas import (
    ClassCreateRequest,
    ClassUpdateRequest,
    ExportBySceneRequest,
    ExtractionRequest,
    SceneCreateRequest,
    SceneUpdateRequest,
    SceneSimpleListResponse,
)

router = APIRouter(prefix="/memory/ontology", tags=["V1 - Ontology API"])
logger = get_business_logger()


def _encode_result(result):
    """Encode result for JSON serialization, preserving Response objects as-is."""
    if isinstance(result, Response):
        return result
    return jsonable_encoder(result)


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


# ==================== 本体提取 ====================


@router.post("/extract")
@require_api_key_self_db(scopes=["memory"])
async def extract_ontology(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """Extract ontology classes from scenario description."""
    body = await request.json()
    payload = ExtractionRequest(**body)

    logger.info(f"V1 ontology extract - workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.extract_ontology(
        request=payload,
        language_type=language_type,
        current_user=current_user,
    )
    return _encode_result(result)


# ==================== 场景管理 ====================


@router.post("/scene")
@require_api_key_self_db(scopes=["memory"])
async def create_scene(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
    x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
):
    """Create a new ontology scene."""
    body = await request.json()
    payload = SceneCreateRequest(**body)

    logger.info(f"V1 create scene - workspace: {api_key_auth.workspace_id}, name: {payload.scene_name}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.create_scene(
        request=payload,
        current_user=current_user,
        x_language_type=x_language_type,
    )
    return _encode_result(result)


@router.put("/scene/{scene_id}")
@require_api_key_self_db(scopes=["memory"])
async def update_scene(
    scene_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
):
    """Update an ontology scene."""
    body = await request.json()
    payload = SceneUpdateRequest(**body)

    logger.info(f"V1 update scene - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.update_scene(
        scene_id=scene_id,
        request=payload,
        current_user=current_user,
    )
    return _encode_result(result)


@router.delete("/scene/{scene_id}")
@require_api_key_self_db(scopes=["memory"])
async def delete_scene(
    scene_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
):
    """Delete an ontology scene and all its classes."""
    logger.info(f"V1 delete scene - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.delete_scene(
        scene_id=scene_id,
        current_user=current_user,
    )
    return _encode_result(result)


@router.get("/scenes/simple", response_model=SceneSimpleListResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_scenes_simple(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
):
    """Get simple scene list (id + name only, for dropdown)."""
    logger.info(f"V1 get scenes simple - workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.get_scenes_simple(
        current_user=current_user,
    )
    return _encode_result(result)


@router.get("/scenes")
@require_api_key_self_db(scopes=["memory"])
async def get_scenes(
    request: Request,
    scene_name: Optional[str] = Query(None, description="Scene name keyword for fuzzy search"),
    page: Optional[int] = Query(None, description="Page number (from 1)"),
    pagesize: Optional[int] = Query(None, description="Page size"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get scene list with pagination and fuzzy search."""
    logger.info(f"V1 get scenes - workspace: {api_key_auth.workspace_id}, keyword: {scene_name}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.get_scenes(
        workspace_id=None,
        scene_name=scene_name,
        page=page,
        pagesize=pagesize,
        current_user=current_user,
    )
    return _encode_result(result)


# ==================== 类型管理 ====================


@router.post("/class")
@require_api_key_self_db(scopes=["memory"])
async def create_class(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
    x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
):
    """Create ontology class(es) in a scene (supports batch)."""
    body = await request.json()
    payload = ClassCreateRequest(**body)

    logger.info(f"V1 create class - workspace: {api_key_auth.workspace_id}, scene_id: {payload.scene_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.create_class(
        request=payload,
        current_user=current_user,
        x_language_type=x_language_type,
    )
    return _encode_result(result)


@router.put("/class/{class_id}")
@require_api_key_self_db(scopes=["memory"])
async def update_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
):
    """Update an ontology class."""
    body = await request.json()
    payload = ClassUpdateRequest(**body)

    logger.info(f"V1 update class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.update_class(
        class_id=class_id,
        request=payload,
        current_user=current_user,
    )
    return _encode_result(result)


@router.delete("/class/{class_id}")
@require_api_key_self_db(scopes=["memory"])
async def delete_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
):
    """Delete an ontology class."""
    logger.info(f"V1 delete class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.delete_class(
        class_id=class_id,
        current_user=current_user,
    )
    return _encode_result(result)


@router.get("/class/{class_id}")
@require_api_key_self_db(scopes=["memory"])
async def get_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
):
    """Get a single ontology class by ID."""
    logger.info(f"V1 get class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.get_class(
        class_id=class_id,
        current_user=current_user,
    )
    return _encode_result(result)


@router.get("/classes")
@require_api_key_self_db(scopes=["memory"])
async def get_classes(
    request: Request,
    scene_id: str = Query(..., description="Scene ID"),
    class_name: Optional[str] = Query(None, description="Class name keyword for fuzzy search"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get class list for a scene with optional fuzzy search."""
    logger.info(f"V1 get classes - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.get_classes(
        scene_id=scene_id,
        class_name=class_name,
        current_user=current_user,
    )
    return _encode_result(result)


# ==================== OWL 导入/导出 ====================


@router.post("/import")
@require_api_key_self_db(scopes=["memory"])
async def import_owl_file(
    request: Request,
    scene_name: str = Form(..., description="Scene name"),
    scene_description: Optional[str] = Form(None, description="Scene description"),
    file: UploadFile = File(..., description="OWL/TTL file"),
    api_key_auth: ApiKeyAuth = None,
):
    """Import OWL/TTL file and create a new scene."""
    logger.info(f"V1 import OWL - workspace: {api_key_auth.workspace_id}, scene_name: {scene_name}")
    current_user = await _resolve_current_user(api_key_auth)

    result = await ontology_controller.import_owl_file(
        scene_name=scene_name,
        scene_description=scene_description,
        file=file,
        current_user=current_user,
    )
    return _encode_result(result)


@router.post("/export")
@require_api_key_self_db(scopes=["memory"])
async def export_owl_by_scene(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
):
    """Export OWL/TTL file by scene."""
    body = await request.json()
    payload = ExportBySceneRequest(**body)

    logger.info(f"V1 export OWL - workspace: {api_key_auth.workspace_id}, scene_id: {payload.scene_id}")
    current_user = await _resolve_current_user(api_key_auth)

    return await ontology_controller.export_owl_by_scene(
        request=payload,
        current_user=current_user,
    )
