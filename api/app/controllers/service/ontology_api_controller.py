"""Ontology 服务接口 — 基于 API Key 认证

包装 ontology_controller.py 中的内部接口，提供基于 API Key 认证的对外服务。

路由前缀: /memory/ontology
最终路径: /v1/memory/ontology/...
认证方式: API Key (@require_api_key)
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.api_key_utils import get_current_user_from_api_key
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth

# 包装内部 controller
from app.controllers import ontology_controller
from app.schemas.ontology_schemas import (
    ExtractionRequest,
    ExportBySceneRequest,
    SceneCreateRequest,
    SceneUpdateRequest,
    ClassCreateRequest,
    ClassUpdateRequest,
)

router = APIRouter(prefix="/memory/ontology", tags=["V1 - Ontology API"])
logger = get_business_logger()


# ==================== 本体提取 ====================


@router.post("/extract")
@require_api_key(scopes=["memory"])
async def extract_ontology(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """Extract ontology classes from scenario description.

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = ExtractionRequest(**body)

    logger.info(f"V1 ontology extract - workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.extract_ontology(
        request=payload,
        language_type=language_type,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


# ==================== 场景管理 ====================


@router.post("/scene")
@require_api_key(scopes=["memory"])
async def create_scene(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
    x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
):
    """Create a new ontology scene.

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = SceneCreateRequest(**body)

    logger.info(f"V1 create scene - workspace: {api_key_auth.workspace_id}, name: {payload.scene_name}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.create_scene(
        request=payload,
        db=db,
        current_user=current_user,
        x_language_type=x_language_type,
    )
    return jsonable_encoder(result)


@router.put("/scene/{scene_id}")
@require_api_key(scopes=["memory"])
async def update_scene(
    scene_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """Update an ontology scene.

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = SceneUpdateRequest(**body)

    logger.info(f"V1 update scene - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.update_scene(
        scene_id=scene_id,
        request=payload,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.delete("/scene/{scene_id}")
@require_api_key(scopes=["memory"])
async def delete_scene(
    scene_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Delete an ontology scene and all its classes.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 delete scene - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.delete_scene(
        scene_id=scene_id,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.get("/scenes/simple")
@require_api_key(scopes=["memory"])
async def get_scenes_simple(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Get simple scene list (id + name only, for dropdown).

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 get scenes simple - workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.get_scenes_simple(
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.get("/scenes")
@require_api_key(scopes=["memory"])
async def get_scenes(
    request: Request,
    scene_name: Optional[str] = Query(None, description="Scene name keyword for fuzzy search"),
    page: Optional[int] = Query(None, description="Page number (from 1)"),
    pagesize: Optional[int] = Query(None, description="Page size"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Get scene list with pagination and fuzzy search.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 get scenes - workspace: {api_key_auth.workspace_id}, keyword: {scene_name}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.get_scenes(
        workspace_id=None,
        scene_name=scene_name,
        page=page,
        pagesize=pagesize,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


# ==================== 类型管理 ====================


@router.post("/class")
@require_api_key(scopes=["memory"])
async def create_class(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
    x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
):
    """Create ontology class(es) in a scene (supports batch).

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = ClassCreateRequest(**body)

    logger.info(f"V1 create class - workspace: {api_key_auth.workspace_id}, scene_id: {payload.scene_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.create_class(
        request=payload,
        db=db,
        current_user=current_user,
        x_language_type=x_language_type,
    )
    return jsonable_encoder(result)


@router.put("/class/{class_id}")
@require_api_key(scopes=["memory"])
async def update_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """Update an ontology class.

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = ClassUpdateRequest(**body)

    logger.info(f"V1 update class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.update_class(
        class_id=class_id,
        request=payload,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.delete("/class/{class_id}")
@require_api_key(scopes=["memory"])
async def delete_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Delete an ontology class.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 delete class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.delete_class(
        class_id=class_id,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.get("/class/{class_id}")
@require_api_key(scopes=["memory"])
async def get_class(
    class_id: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Get a single ontology class by ID.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 get class - class_id: {class_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.get_class(
        class_id=class_id,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.get("/classes")
@require_api_key(scopes=["memory"])
async def get_classes(
    request: Request,
    scene_id: str = Query(..., description="Scene ID"),
    class_name: Optional[str] = Query(None, description="Class name keyword for fuzzy search"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Get class list for a scene with optional fuzzy search.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 get classes - scene_id: {scene_id}, workspace: {api_key_auth.workspace_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.get_classes(
        scene_id=scene_id,
        class_name=class_name,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


# ==================== OWL 导入/导出 ====================


@router.post("/import")
@require_api_key(scopes=["memory"])
async def import_owl_file(
    request: Request,
    scene_name: str = Form(..., description="Scene name"),
    scene_description: Optional[str] = Form(None, description="Scene description"),
    file: UploadFile = File(..., description="OWL/TTL file"),
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """Import OWL/TTL file and create a new scene.

    Requires API Key with 'memory' scope.
    """
    logger.info(f"V1 import OWL - workspace: {api_key_auth.workspace_id}, scene_name: {scene_name}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    result = await ontology_controller.import_owl_file(
        scene_name=scene_name,
        scene_description=scene_description,
        file=file,
        db=db,
        current_user=current_user,
    )
    return jsonable_encoder(result)


@router.post("/export")
@require_api_key(scopes=["memory"])
async def export_owl_by_scene(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """Export OWL/TTL file by scene.

    Requires API Key with 'memory' scope.
    """
    body = await request.json()
    payload = ExportBySceneRequest(**body)

    logger.info(f"V1 export OWL - workspace: {api_key_auth.workspace_id}, scene_id: {payload.scene_id}")

    current_user = get_current_user_from_api_key(db, api_key_auth)

    return await ontology_controller.export_owl_by_scene(
        request=payload,
        db=db,
        current_user=current_user,
    )