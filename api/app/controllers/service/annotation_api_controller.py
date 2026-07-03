"""Annotation 服务接口 - 基于 API Key 认证 (v1)

路由前缀: /v1/app/annotations
认证方式: API Key (scope: app)
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.schemas import annotation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import PageData, PageMeta
from app.services import api_key_service
from app.services.annotation_service import AnnotationService

router = APIRouter(prefix="/app/annotations", tags=["V1 - Annotation API"])
logger = get_business_logger()


def _get_annotation_service_with_app_context(
    api_key_auth: ApiKeyAuth,
    db: Session,
    check_enabled: bool = False,
):
    """从 API Key 认证信息构建标注服务上下文，返回 (service, app_id, workspace_id, current_user_id)"""
    app_id = api_key_auth.resource_id
    workspace_id = api_key_auth.workspace_id

    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, workspace_id)
    current_user_id = api_key.creator.id

    service = AnnotationService(db)

    if check_enabled:
        setting = service.get_setting(app_id)
        if not setting or setting.enabled != 1:
            raise BusinessException(
                "Annotation feature is not enabled. Please enable it in the annotation settings and configure the Embedding model.",
                BizCode.BAD_REQUEST,
            )
        return service, app_id, workspace_id, current_user_id, setting

    return service, app_id, workspace_id, current_user_id


@router.get("", summary="获取标注列表")
@require_api_key(scopes=["app"])
async def list_annotations(
    request: Request,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="搜索关键词（匹配问题或答案）"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(20, ge=1, le=100, description="每页数量，最大 100"),
):
    """获取当前应用的标注列表，支持分页和关键词搜索。"""
    service, app_id, _, _ = _get_annotation_service_with_app_context(api_key_auth, db)
    items, total = service.list_annotations(app_id, search, page, pagesize)

    data = [annotation_schema.AnnotationListItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data).model_dump(mode="json"))


@router.post("", summary="创建标注")
@require_api_key(scopes=["app"])
async def create_annotation(
    request: Request,
    payload: annotation_schema.AnnotationCreate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
):
    """创建一个新的标注（QA问答对）。

    如果标注功能已启用Embedding模型，会自动为问题生成向量用于相似度匹配。
    """
    service, app_id, workspace_id, current_user_id, setting = _get_annotation_service_with_app_context(
        api_key_auth, db, check_enabled=True
    )

    embedding = None
    try:
        if setting.model_config_id:
            from app.models.models_model import ModelConfig
            from app.services.model_service import ModelApiKeyService
            model_config = db.query(ModelConfig).filter(ModelConfig.id == setting.model_config_id).first()
            if model_config:
                api_key_obj = ModelApiKeyService.get_available_api_key(db, setting.model_config_id)
                if api_key_obj:
                    from app.core.models.base import RedBearModelConfig
                    config = RedBearModelConfig(
                        model_name=api_key_obj.model_name,
                        provider=api_key_obj.provider,
                        api_key=api_key_obj.api_key,
                        base_url=api_key_obj.api_base or None,
                        timeout=60,
                        max_retries=3,
                    )
                    embedding = service.generate_embedding(payload.question, config)
    except Exception as e:
        logger.warning(f"生成Embedding失败，继续创建标注: {e}")

    annotation = service.create_annotation(
        app_id=app_id,
        workspace_id=workspace_id,
        created_by=current_user_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    return success(data=annotation_schema.Annotation.model_validate(annotation).model_dump(mode="json"))


# ==================== Annotation Settings ====================

@router.get("/settings", summary="获取标注设置")
@require_api_key(scopes=["app"])
async def get_annotation_settings(
    request: Request,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
):
    """获取当前应用的标注设置。"""
    service, app_id, workspace_id, _ = _get_annotation_service_with_app_context(api_key_auth, db)
    setting = service.get_setting(app_id)
    if not setting:
        return success(data={
            "app_id": str(app_id),
            "workspace_id": str(workspace_id),
            "similarity_threshold": 0.85,
            "model_config_id": None,
            "enabled": 0,
        })
    return success(data=annotation_schema.AnnotationSettingResponse(
        app_id=str(setting.app_id),
        workspace_id=str(setting.workspace_id),
        similarity_threshold=setting.similarity_threshold,
        model_config_id=str(setting.model_config_id) if setting.model_config_id else None,
        enabled=setting.enabled,
    ).model_dump())


@router.put("/settings", summary="更新标注设置")
@require_api_key(scopes=["app"])
async def update_annotation_settings(
    request: Request,
    payload: annotation_schema.AnnotationSettingUpdate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
):
    """更新当前应用的标注设置（相似度阈值、Embedding模型、启用/禁用）。"""
    service, app_id, workspace_id, _ = _get_annotation_service_with_app_context(api_key_auth, db)
    setting = service.update_setting(
        app_id=app_id,
        workspace_id=workspace_id,
        similarity_threshold=payload.similarity_threshold,
        model_config_id=payload.model_config_id,
        enabled=payload.enabled,
    )
    return success(data=annotation_schema.AnnotationSettingResponse(
        app_id=str(setting.app_id),
        workspace_id=str(setting.workspace_id),
        similarity_threshold=setting.similarity_threshold,
        model_config_id=str(setting.model_config_id) if setting.model_config_id else None,
        enabled=setting.enabled,
    ).model_dump())


@router.put("/{annotation_id}", summary="更新标注")
@require_api_key(scopes=["app"])
async def update_annotation(
    request: Request,
    annotation_id: uuid.UUID,
    payload: annotation_schema.AnnotationUpdate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
):
    """更新指定标注的问题和/或答案。

    如果更新了问题且已配置Embedding模型，会自动重新生成向量。
    """
    service, app_id, _, _, setting = _get_annotation_service_with_app_context(
        api_key_auth, db, check_enabled=True
    )

    # 校验标注归属
    existing = service.get_annotation(annotation_id)
    if not existing or str(existing.app_id) != str(app_id):
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)

    embedding = None
    if payload.question:
        try:
            if setting.model_config_id:
                from app.models.models_model import ModelConfig
                from app.services.model_service import ModelApiKeyService
                model_config = db.query(ModelConfig).filter(ModelConfig.id == setting.model_config_id).first()
                if model_config:
                    api_key_obj = ModelApiKeyService.get_available_api_key(db, setting.model_config_id)
                    if api_key_obj:
                        from app.core.models.base import RedBearModelConfig
                        config = RedBearModelConfig(
                            model_name=api_key_obj.model_name,
                            provider=api_key_obj.provider,
                            api_key=api_key_obj.api_key,
                            base_url=api_key_obj.api_base or None,
                            timeout=60,
                            max_retries=3,
                        )
                        embedding = service.generate_embedding(payload.question, config)
        except Exception as e:
            logger.warning(f"重新生成Embedding失败: {e}")

    annotation = service.update_annotation(
        annotation_id=annotation_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    if not annotation:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)
    return success(data=annotation_schema.Annotation.model_validate(annotation).model_dump(mode="json"))


@router.delete("/{annotation_id}", summary="删除标注")
@require_api_key(scopes=["app"])
async def delete_annotation(
    request: Request,
    annotation_id: uuid.UUID,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key 注入；lambda:None 仅防被 FastAPI 当 body 字段
    db: Session = Depends(get_db),
):
    """删除指定标注（软删除）。"""
    service, app_id, _, _ = _get_annotation_service_with_app_context(api_key_auth, db)

    existing = service.get_annotation(annotation_id)
    if not existing or str(existing.app_id) != str(app_id):
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)

    success_delete = service.delete_annotation(annotation_id)
    if not success_delete:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)
    return success(msg="标注删除成功")
