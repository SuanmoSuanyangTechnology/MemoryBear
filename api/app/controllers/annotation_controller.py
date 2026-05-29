import csv
import io
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import User
from app.schemas import annotation_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services.annotation_service import AnnotationService

router = APIRouter(prefix="/apps/{app_id}/annotations", tags=["Annotations"])
logger = get_business_logger()


@router.post("", summary="创建标注")
@cur_workspace_access_guard()
def create_annotation(
        app_id: uuid.UUID,
        payload: annotation_schema.AnnotationCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    service = AnnotationService(db)

    setting = service.get_setting(app_id)
    if not setting or setting.enabled != 1:
        raise BusinessException("请先在标注设置中启用标注功能并配置Embedding模型", BizCode.BAD_REQUEST)

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
        created_by=current_user.id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding
    )
    return success(data=annotation_schema.Annotation.model_validate(annotation))


@router.get("/{annotation_id}/hit-logs", summary="获取标注命中历史")
@cur_workspace_access_guard()
def get_annotation_hit_logs(
        app_id: uuid.UUID,
        annotation_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)

    items, total = service.list_hit_logs(annotation_id, page, pagesize)
    data = [annotation_schema.AnnotationHitLogItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data))


@router.get("", summary="获取标注列表")
@cur_workspace_access_guard()
def list_annotations(
        app_id: uuid.UUID,
        search: Optional[str] = None,
        page: int = 1,
        pagesize: int = 20,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    items, total = service.list_annotations(app_id, search, page, pagesize)

    data = [annotation_schema.AnnotationListItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data))


# ==================== Annotation Settings (must be before {annotation_id} routes) ====================

@router.get("/settings", summary="获取标注设置")
@cur_workspace_access_guard()
def get_annotation_settings(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    setting = service.get_setting(app_id)
    if not setting:
        return success(data={
            "app_id": str(app_id),
            "workspace_id": str(current_user.current_workspace_id),
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
    ))


@router.put("/settings", summary="更新标注设置")
@cur_workspace_access_guard()
def update_annotation_settings(
        app_id: uuid.UUID,
        payload: annotation_schema.AnnotationSettingUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    service = AnnotationService(db)
    setting = service.update_setting(
        app_id=app_id,
        workspace_id=workspace_id,
        similarity_threshold=payload.similarity_threshold,
        model_config_id=payload.model_config_id,
        enabled=payload.enabled
    )
    return success(data=annotation_schema.AnnotationSettingResponse(
        app_id=str(setting.app_id),
        workspace_id=str(setting.workspace_id),
        similarity_threshold=setting.similarity_threshold,
        model_config_id=str(setting.model_config_id) if setting.model_config_id else None,
        enabled=setting.enabled,
    ))


# ==================== Batch Operations (must be before {annotation_id} routes) ====================

@router.get("/export", summary="批量导出标注")
@cur_workspace_access_guard()
def export_annotations(
        app_id: uuid.UUID,
        format: str = Query("csv", regex="^(csv|json)$", description="导出格式: csv 或 json"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    annotations = service.export_all(app_id)

    items = [{"问题": a.question, "答案": a.answer} for a in annotations]

    if format == "json":
        json_str = json.dumps(items, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.StringIO(json_str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=annotations.json"}
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["问题", "答案"])
    writer.writeheader()
    writer.writerows(items)
    csv_content = output.getvalue()
    output.close()

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=annotations.csv"}
    )


# ==================== Annotation by ID ====================

@router.get("/{annotation_id}", summary="获取标注详情")
@cur_workspace_access_guard()
def get_annotation(
        app_id: uuid.UUID,
        annotation_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)
    return success(data=annotation_schema.Annotation.model_validate(annotation))


@router.put("/{annotation_id}", summary="更新标注")
@cur_workspace_access_guard()
def update_annotation(
        app_id: uuid.UUID,
        annotation_id: uuid.UUID,
        payload: annotation_schema.AnnotationUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)

    setting = service.get_setting(app_id)
    if not setting or setting.enabled != 1:
        raise BusinessException("请先在标注设置中启用标注功能并配置Embedding模型", BizCode.BAD_REQUEST)

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
        embedding=embedding
    )
    if not annotation:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)
    return success(data=annotation_schema.Annotation.model_validate(annotation))


@router.delete("", summary="删除所有标注")
@cur_workspace_access_guard()
def delete_all_annotations(
        app_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    result = service.delete_all(app_id)
    return success(data=annotation_schema.BatchDeleteResult(count=result["count"]))


@router.delete("/{annotation_id}", summary="删除标注")
@cur_workspace_access_guard()
def delete_annotation(
        app_id: uuid.UUID,
        annotation_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    service = AnnotationService(db)
    success_delete = service.delete_annotation(annotation_id)
    if not success_delete:
        raise BusinessException("标注不存在", BizCode.NOT_FOUND)
    return success(msg="标注删除成功")


@router.post("/import", summary="批量导入标注")
@cur_workspace_access_guard()
def import_annotations(
        app_id: uuid.UUID,
        file: UploadFile = File(..., description="CSV文件，列：问题, 答案"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    workspace_id = current_user.current_workspace_id
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise BusinessException("仅支持CSV格式文件", BizCode.BAD_REQUEST)

    content = file.file.read()
    try:
        content_str = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BusinessException("文件编码不是UTF-8", BizCode.BAD_REQUEST)

    reader = csv.DictReader(io.StringIO(content_str))

    items = []
    for row_num, row in enumerate(reader, start=2):
        question = (row.get("问题") or row.get("question") or "").strip()
        answer = (row.get("答案") or row.get("answer") or "").strip()
        if not question or not answer:
            logger.warning(f"第{row_num}行缺少问题或答案，已跳过")
            continue
        if len(question) > 5000 or len(answer) > 10000:
            logger.warning(f"第{row_num}行问题或答案超长，已跳过")
            continue
        items.append({"question": question, "answer": answer})

    if not items:
        raise BusinessException("CSV文件中没有有效的标注数据", BizCode.BAD_REQUEST)

    service = AnnotationService(db)

    setting = service.get_setting(app_id)
    if not setting or setting.enabled != 1:
        raise BusinessException("请先在标注设置中启用标注功能并配置Embedding模型", BizCode.BAD_REQUEST)

    from app.models.models_model import ModelConfig
    from app.services.model_service import ModelApiKeyService
    from app.core.models.base import RedBearModelConfig

    model_cfg = None
    if setting.model_config_id:
        model_cfg = db.query(ModelConfig).filter(ModelConfig.id == setting.model_config_id).first()

    api_key_obj = None
    config = None
    if model_cfg:
        api_key_obj = ModelApiKeyService.get_available_api_key(db, setting.model_config_id)
        if api_key_obj:
            config = RedBearModelConfig(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                base_url=api_key_obj.api_base or None,
                timeout=60,
                max_retries=3,
            )

    for item in items:
        if config:
            try:
                item["embedding"] = service.generate_embedding(item["question"], config)
            except Exception as e:
                logger.warning(f"生成Embedding失败，跳过: {item['question'][:50]}... error={e}")
                item["embedding"] = None
        else:
            item["embedding"] = None

    result = service.batch_import(
        app_id=app_id,
        workspace_id=workspace_id,
        created_by=current_user.id,
        items=items,
    )

    return success(data=annotation_schema.BatchImportResult(count=result["count"]))