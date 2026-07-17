"""Annotation 服务接口 - 基于 API Key 认证 (v1)

路由前缀: /v1/app/annotations
认证方式: API Key (scope: app)
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_key_auth import require_api_key_self_db
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_async_db
from app.models.annotation_model import AppAnnotation, AppAnnotationSetting
from app.schemas import annotation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import PageData, PageMeta
from app.services import api_key_service
from app.services.annotation_service import AnnotationService

router = APIRouter(prefix="/app/annotations", tags=["V1 - Annotation API"])
logger = get_business_logger()


async def _get_annotation_context(
    api_key_auth: ApiKeyAuth,
    db: AsyncSession,
    check_enabled: bool = False,
):
    """从 API Key 认证信息构建标注上下文。"""
    app_id = api_key_auth.resource_id
    workspace_id = api_key_auth.workspace_id

    api_key = await api_key_service.ApiKeyService.get_api_key_async(
        db, api_key_auth.api_key_id, workspace_id
    )
    current_user_id = api_key.creator.id

    setting = None
    if check_enabled:
        setting = await db.scalar(
            select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id)
        )
        if not setting or setting.enabled != 1:
            raise BusinessException(
                "Annotation feature is not enabled. Please enable it in the annotation settings and configure the Embedding model.",
                BizCode.BAD_REQUEST,
            )

    return app_id, workspace_id, current_user_id, setting


async def _generate_embedding(
    db: AsyncSession,
    model_config_id: uuid.UUID,
    text: str,
):
    """加载 Embedding 模型配置并生成向量。"""
    from app.core.models.base import RedBearModelConfig
    from app.services.model_service import ModelApiKeyService

    api_key_obj = await ModelApiKeyService.get_available_api_key_async(db, model_config_id)
    if not api_key_obj:
        return None

    config = RedBearModelConfig(
        model_name=api_key_obj.model_name,
        provider=api_key_obj.provider,
        api_key=api_key_obj.api_key,
        base_url=api_key_obj.api_base or None,
        timeout=60,
        max_retries=3,
    )
    return AnnotationService(db).generate_embedding(text, config)


@router.get("", summary="获取标注列表")
@require_api_key_self_db(scopes=["app"])
async def list_annotations(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: AsyncSession = Depends(get_async_db),
    search: Optional[str] = Query(None, description="搜索关键词（匹配问题或答案）"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(20, ge=1, le=100, description="每页数量，最大 100"),
):
    """获取当前应用的标注列表，支持分页和关键词搜索。"""
    app_id, _, _, _ = await _get_annotation_context(api_key_auth, db)
    conditions = [AppAnnotation.app_id == app_id, AppAnnotation.is_active == 1]
    if search:
        conditions.append(
            or_(
                AppAnnotation.question.ilike(f"%{search}%"),
                AppAnnotation.answer.ilike(f"%{search}%"),
            )
        )

    total = await db.scalar(
        select(func.count()).select_from(AppAnnotation).where(*conditions)
    )
    result = await db.execute(
        select(AppAnnotation)
        .where(*conditions)
        .order_by(AppAnnotation.created_at.desc())
        .offset((page - 1) * pagesize)
        .limit(pagesize)
    )
    items = result.scalars().all()

    data = [annotation_schema.AnnotationListItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total or 0, hasnext=(page * pagesize) < (total or 0))
    return success(data=PageData(page=meta, items=data).model_dump(mode="json"))


@router.post("", summary="创建标注")
@require_api_key_self_db(scopes=["app"])
async def create_annotation(
    request: Request,
    payload: annotation_schema.AnnotationCreate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key_self_db 注入；用 Depends 避免与 payload 一起被当作嵌套 Body 字段
    db: AsyncSession = Depends(get_async_db),
):
    """创建一个新的标注（QA问答对）。

    如果标注功能已启用Embedding模型，会自动为问题生成向量用于相似度匹配。
    """
    app_id, workspace_id, current_user_id, setting = await _get_annotation_context(
        api_key_auth, db, check_enabled=True
    )

    embedding = None
    try:
        if setting.model_config_id:
            embedding = await _generate_embedding(db, setting.model_config_id, payload.question)
    except Exception as e:
        logger.warning(f"生成Embedding失败，继续创建标注: {e}")

    annotation = AppAnnotation(
        app_id=app_id,
        workspace_id=workspace_id,
        created_by=current_user_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
        hit_count=0,
        is_active=1,
    )
    db.add(annotation)
    await db.commit()
    await db.refresh(annotation)
    return success(data=annotation_schema.Annotation.model_validate(annotation).model_dump(mode="json"))


# ==================== Annotation Settings ====================

@router.get("/settings", summary="获取标注设置")
@require_api_key_self_db(scopes=["app"])
async def get_annotation_settings(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: AsyncSession = Depends(get_async_db),
):
    """获取当前应用的标注设置。"""
    app_id, workspace_id, _, _ = await _get_annotation_context(api_key_auth, db)
    setting = await db.scalar(
        select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id)
    )
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
@require_api_key_self_db(scopes=["app"])
async def update_annotation_settings(
    request: Request,
    payload: annotation_schema.AnnotationSettingUpdate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key_self_db 注入；用 Depends 避免与 payload 一起被当作嵌套 Body 字段
    db: AsyncSession = Depends(get_async_db),
):
    """更新当前应用的标注设置（相似度阈值、Embedding模型、启用/禁用）。"""
    app_id, workspace_id, _, _ = await _get_annotation_context(api_key_auth, db)
    setting = await db.scalar(
        select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id)
    )
    if setting:
        if payload.similarity_threshold is not None:
            setting.similarity_threshold = payload.similarity_threshold
        if payload.model_config_id is not None:
            setting.model_config_id = payload.model_config_id
        if payload.enabled is not None:
            setting.enabled = payload.enabled
    else:
        setting = AppAnnotationSetting(
            app_id=app_id,
            workspace_id=workspace_id,
            similarity_threshold=payload.similarity_threshold or 0.85,
            model_config_id=payload.model_config_id,
            enabled=payload.enabled if payload.enabled is not None else 0,
        )
        db.add(setting)

    await db.commit()
    await db.refresh(setting)
    return success(data=annotation_schema.AnnotationSettingResponse(
        app_id=str(setting.app_id),
        workspace_id=str(setting.workspace_id),
        similarity_threshold=setting.similarity_threshold,
        model_config_id=str(setting.model_config_id) if setting.model_config_id else None,
        enabled=setting.enabled,
    ).model_dump())


@router.put("/{annotation_id}", summary="更新标注")
@require_api_key_self_db(scopes=["app"])
async def update_annotation(
    request: Request,
    annotation_id: uuid.UUID,
    payload: annotation_schema.AnnotationUpdate,
    api_key_auth: ApiKeyAuth = Depends(lambda: None),  # 真实值由 @require_api_key_self_db 注入；用 Depends 避免与 payload 一起被当作嵌套 Body 字段
    db: AsyncSession = Depends(get_async_db),
):
    """更新指定标注的问题和/或答案。

    如果更新了问题且已配置Embedding模型，会自动重新生成向量。
    """
    app_id, _, _, setting = await _get_annotation_context(
        api_key_auth, db, check_enabled=True
    )

    annotation = await db.scalar(
        select(AppAnnotation).where(
            AppAnnotation.id == annotation_id,
            AppAnnotation.is_active == 1,
        )
    )
    if not annotation or str(annotation.app_id) != str(app_id):
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)

    embedding = None
    if payload.question:
        try:
            if setting.model_config_id:
                embedding = await _generate_embedding(db, setting.model_config_id, payload.question)
        except Exception as e:
            logger.warning(f"重新生成Embedding失败: {e}")

    if payload.question is not None:
        annotation.question = payload.question
    if payload.answer is not None:
        annotation.answer = payload.answer
    if embedding is not None:
        annotation.embedding = embedding

    await db.commit()
    await db.refresh(annotation)
    return success(data=annotation_schema.Annotation.model_validate(annotation).model_dump(mode="json"))


@router.delete("/{annotation_id}", summary="删除标注")
@require_api_key_self_db(scopes=["app"])
async def delete_annotation(
    request: Request,
    annotation_id: uuid.UUID,
    api_key_auth: ApiKeyAuth = None,
    db: AsyncSession = Depends(get_async_db),
):
    """删除指定标注（软删除）。"""
    app_id, _, _, _ = await _get_annotation_context(api_key_auth, db)

    annotation = await db.scalar(
        select(AppAnnotation).where(
            AppAnnotation.id == annotation_id,
            AppAnnotation.is_active == 1,
        )
    )
    if not annotation or str(annotation.app_id) != str(app_id):
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)

    annotation.is_active = 0
    await db.commit()
    return success(msg="标注删除成功")
