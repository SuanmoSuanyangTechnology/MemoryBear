"""Annotation routes (frozen port of the monolith ``annotation_controller`` and
``service/annotation_api_controller``).

Two faces live in this module: the manager face (``/apps/{app_id}/annotations``,
workspace-guarded) and the ``/v1`` API-key face (``/app/annotations``); the
gateway rewrites both onto ``/internal/v1``. On the ``/v1`` face the gateway
terminates the key but forwards neither the key's resource binding nor the
monolith's self-db pipeline, so the service resolves the binding from the
injected key id and re-applies the publish-state and scope checks. Embedding
generation calls model-service directly (east-west); whenever resolution or the
call fails the write degrades to a missing vector, exactly like the host.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...guards.workspace_access import require_workspace_access
from ...integrations.model_invoke import ModelInvokeClient
from ...models.annotation_model import AppAnnotationSetting
from ...models.app_model import App
from ...repositories import cross_domain_read
from ...schemas.annotation_schema import (
    Annotation,
    AnnotationCreate,
    AnnotationHitLogItem,
    AnnotationListItem,
    AnnotationSettingResponse,
    AnnotationSettingUpdate,
    AnnotationUpdate,
    BatchDeleteResult,
    BatchImportResult,
)
from ...services.annotation_service import AnnotationService
from ..dependencies import (
    API_KEY_ID_HEADER,
    Principal,
    get_model_invoke,
    get_principal,
    get_sync_db,
)
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps/{app_id}/annotations", tags=["Annotations"])
v1_router = APIRouter(prefix="/app/annotations", tags=["Annotation API (v1)"])

DbSession = Annotated[Session, Depends(get_sync_db)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
Caller = Annotated[Principal, Depends(get_principal)]
ModelInvoke = Annotated[ModelInvokeClient, Depends(get_model_invoke)]

# Host messages, kept verbatim on the wire (the manager and /v1 faces each have
# their own enable-required text).
ENABLE_REQUIRED_MSG = "请先在标注设置中启用标注功能并配置Embedding模型"
ENABLE_REQUIRED_MSG_EN = (
    "Annotation feature is not enabled. "
    "Please enable it in the annotation settings and configure the Embedding model."
)
ANNOTATION_NOT_FOUND_MSG = "标注不存在"


def _generate_question_embedding(
    service: AnnotationService,
    question: str,
    *,
    model_config_id: UUID,
    principal: Principal,
    workspace_id: UUID | None,
    client: ModelInvokeClient,
    failure_log: str,
) -> list[float] | None:
    """Best-effort embedding for one question.

    The host resolves the model reference and invokes the transport inside one
    try; any failure logs and continues with a missing vector.
    """

    try:
        ref = service.resolve_embedding_ref(model_config_id, principal.tenant_id)
        return service.generate_embedding(
            question,
            ref=ref,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            actor_id=principal.actor_id,
            client=client,
        )
    except Exception as error:
        logger.warning(failure_log, error)
        return None


def _require_annotation_enabled(
    service: AnnotationService, app_id: UUID
) -> AppAnnotationSetting:
    """Fetch the app's annotation setting and enforce the enable gate."""

    setting = service.get_setting(app_id)
    if not setting or setting.enabled != 1:
        raise BusinessException(ENABLE_REQUIRED_MSG, BizCode.BAD_REQUEST)
    return setting


def _setting_response(setting: AppAnnotationSetting) -> AnnotationSettingResponse:
    return AnnotationSettingResponse(
        app_id=str(setting.app_id),
        workspace_id=str(setting.workspace_id),
        similarity_threshold=setting.similarity_threshold,
        model_config_id=str(setting.model_config_id) if setting.model_config_id else None,
        enabled=setting.enabled,
    )


# ==================== Manager face ====================


@router.post("", summary="创建标注", response_model=ApiResponse)
def create_annotation(
    app_id: UUID,
    payload: AnnotationCreate,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
    model_invoke: ModelInvoke,
):
    service = AnnotationService(db)
    setting = _require_annotation_enabled(service, app_id)

    embedding = None
    if setting.model_config_id:
        embedding = _generate_question_embedding(
            service,
            payload.question,
            model_config_id=setting.model_config_id,
            principal=principal,
            workspace_id=workspace_id,
            client=model_invoke,
            failure_log="生成Embedding失败，继续创建标注: %s",
        )

    annotation = service.create_annotation(
        app_id=app_id,
        workspace_id=workspace_id,
        created_by=principal.actor_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    return success(data=Annotation.model_validate(annotation))


@router.get("/{annotation_id}/hit-logs", summary="获取标注命中历史", response_model=ApiResponse)
def get_annotation_hit_logs(
    app_id: UUID,
    annotation_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    page: int = 1,
    pagesize: int = 20,
):
    service = AnnotationService(db)
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)

    items, total = service.list_hit_logs(annotation_id, page, pagesize)
    data = [AnnotationHitLogItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data))


@router.get("", summary="获取标注列表", response_model=ApiResponse)
def list_annotations(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    search: str | None = None,
    page: int = 1,
    pagesize: int = 20,
):
    service = AnnotationService(db)
    items, total = service.list_annotations(app_id, search, page, pagesize)

    data = [AnnotationListItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data))


# ==================== Annotation settings (before /{annotation_id}) ====================


@router.get("/settings", summary="获取标注设置", response_model=ApiResponse)
def get_annotation_settings(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    service = AnnotationService(db)
    setting = service.get_setting(app_id)
    if not setting:
        return success(
            data={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "similarity_threshold": 0.85,
                "model_config_id": None,
                "enabled": 0,
            }
        )
    return success(data=_setting_response(setting))


@router.put("/settings", summary="更新标注设置", response_model=ApiResponse)
def update_annotation_settings(
    app_id: UUID,
    payload: AnnotationSettingUpdate,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    service = AnnotationService(db)
    setting = service.update_setting(
        app_id=app_id,
        workspace_id=workspace_id,
        similarity_threshold=payload.similarity_threshold,
        model_config_id=payload.model_config_id,
        enabled=payload.enabled,
    )
    return success(data=_setting_response(setting))


# ==================== Batch operations (before /{annotation_id}) ====================


@router.get("/export", summary="批量导出标注")
def export_annotations(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    format: str = Query("csv", pattern="^(csv|json)$", description="导出格式: csv 或 json"),
):
    service = AnnotationService(db)
    annotations = service.export_all(app_id)
    items = [{"问题": a.question, "答案": a.answer} for a in annotations]

    if format == "json":
        json_str = json.dumps(items, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.StringIO(json_str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=annotations.json"},
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
        headers={"Content-Disposition": "attachment; filename=annotations.csv"},
    )


# ==================== Annotation by id ====================


@router.get("/{annotation_id}", summary="获取标注详情", response_model=ApiResponse)
def get_annotation(
    app_id: UUID,
    annotation_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    service = AnnotationService(db)
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)
    return success(data=Annotation.model_validate(annotation))


@router.put("/{annotation_id}", summary="更新标注", response_model=ApiResponse)
def update_annotation(
    app_id: UUID,
    annotation_id: UUID,
    payload: AnnotationUpdate,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
    model_invoke: ModelInvoke,
):
    service = AnnotationService(db)
    setting = _require_annotation_enabled(service, app_id)

    embedding = None
    if payload.question and setting.model_config_id:
        embedding = _generate_question_embedding(
            service,
            payload.question,
            model_config_id=setting.model_config_id,
            principal=principal,
            workspace_id=workspace_id,
            client=model_invoke,
            failure_log="重新生成Embedding失败: %s",
        )

    annotation = service.update_annotation(
        annotation_id=annotation_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    if not annotation:
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)
    return success(data=Annotation.model_validate(annotation))


@router.delete("", summary="删除所有标注", response_model=ApiResponse)
def delete_all_annotations(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    result = AnnotationService(db).delete_all(app_id)
    return success(data=BatchDeleteResult(count=result["count"]))


@router.delete("/{annotation_id}", summary="删除标注", response_model=ApiResponse)
def delete_annotation(
    app_id: UUID,
    annotation_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    deleted = AnnotationService(db).delete_annotation(annotation_id)
    if not deleted:
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)
    return success(msg="标注删除成功")


@router.post("/import", summary="批量导入标注", response_model=ApiResponse)
def import_annotations(
    app_id: UUID,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
    model_invoke: ModelInvoke,
    file: Annotated[UploadFile, File(description="CSV文件，列：问题, 答案")],
):
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise BusinessException("仅支持CSV格式文件", BizCode.BAD_REQUEST)

    content = file.file.read()
    try:
        content_str = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BusinessException("文件编码不是UTF-8", BizCode.BAD_REQUEST) from error

    reader = csv.DictReader(io.StringIO(content_str))

    items = []
    for row_num, row in enumerate(reader, start=2):
        question = (row.get("问题") or row.get("question") or "").strip()
        answer = (row.get("答案") or row.get("answer") or "").strip()
        if not question or not answer:
            logger.warning("第%s行缺少问题或答案，已跳过", row_num)
            continue
        if len(question) > 5000 or len(answer) > 10000:
            logger.warning("第%s行问题或答案超长，已跳过", row_num)
            continue
        items.append({"question": question, "answer": answer})

    if not items:
        raise BusinessException("CSV文件中没有有效的标注数据", BizCode.BAD_REQUEST)

    service = AnnotationService(db)
    setting = _require_annotation_enabled(service, app_id)

    # The reference is resolved once for the whole batch (no per-row read);
    # each row's invoke degrades independently, exactly like the host.
    ref = None
    if setting.model_config_id:
        ref = service.resolve_embedding_ref(setting.model_config_id, principal.tenant_id)

    for item in items:
        if ref:
            try:
                item["embedding"] = service.generate_embedding(
                    item["question"],
                    ref=ref,
                    tenant_id=principal.tenant_id,
                    workspace_id=workspace_id,
                    actor_id=principal.actor_id,
                    client=model_invoke,
                )
            except Exception as error:
                logger.warning(
                    "生成Embedding失败，跳过: %s... error=%s", item["question"][:50], error
                )
                item["embedding"] = None
        else:
            item["embedding"] = None

    result = service.batch_import(
        app_id=app_id,
        workspace_id=workspace_id,
        created_by=principal.actor_id,
        items=items,
    )
    return success(data=BatchImportResult(count=result["count"]))


# ==================== /v1 API-key face ====================


def _resolve_v1_app(request: Request, db: Session, principal: Principal) -> UUID | None:
    """Resolve the bound application of a ``/v1`` call.

    The gateway terminates the key and injects its row id (the principal carries
    the key creator), but neither the key's resource binding nor the monolith's
    self-db pipeline; the service re-applies both here: key lookup scoped by the
    principal's workspace, then publish-state and scope checks in the host's
    order. The ``service`` key type skips the publish check (not app-bound);
    keys without a resource keep the host's ``None`` passthrough.
    """

    raw_key_id = request.headers.get(API_KEY_ID_HEADER)
    if not raw_key_id:
        raise BusinessException("API Key 不存在", BizCode.API_KEY_NOT_FOUND)
    try:
        api_key_id = UUID(raw_key_id)
    except (ValueError, TypeError) as error:
        raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID) from error

    workspace_id = principal.workspace_id
    binding = (
        cross_domain_read.get_api_key_binding(db, api_key_id, workspace_id)
        if workspace_id is not None
        else None
    )
    if binding is None:
        raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID)

    if binding.resource_id and binding.type != "service":
        app = db.get(App, binding.resource_id)
        if not app or not app.current_release_id:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    if "app" not in (binding.scopes or []):
        raise BusinessException("缺少必须的权限范围：app", BizCode.API_KEY_INVALID_SCOPE)

    return binding.resource_id


def _require_v1_annotation_enabled(
    service: AnnotationService, app_id: UUID
) -> AppAnnotationSetting:
    """``/v1`` variant of the enable gate (English message)."""

    setting = service.get_setting(app_id)
    if not setting or setting.enabled != 1:
        raise BusinessException(ENABLE_REQUIRED_MSG_EN, BizCode.BAD_REQUEST)
    return setting


@v1_router.get("", summary="获取标注列表", response_model=ApiResponse)
def v1_list_annotations(
    request: Request,
    principal: Caller,
    db: DbSession,
    search: Annotated[str | None, Query(description="搜索关键词（匹配问题或答案）")] = None,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100, description="每页数量，最大 100")] = 20,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    items, total = service.list_annotations(app_id, search, page, pagesize)
    data = [AnnotationListItem.model_validate(item) for item in items]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=data))


@v1_router.post("", summary="创建标注", response_model=ApiResponse)
def v1_create_annotation(
    request: Request,
    payload: AnnotationCreate,
    principal: Caller,
    db: DbSession,
    model_invoke: ModelInvoke,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    setting = _require_v1_annotation_enabled(service, app_id)

    embedding = None
    if setting.model_config_id:
        embedding = _generate_question_embedding(
            service,
            payload.question,
            model_config_id=setting.model_config_id,
            principal=principal,
            workspace_id=principal.workspace_id,
            client=model_invoke,
            failure_log="生成Embedding失败，继续创建标注: %s",
        )

    annotation = service.create_annotation(
        app_id=app_id,
        workspace_id=principal.workspace_id,
        created_by=principal.actor_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    return success(data=Annotation.model_validate(annotation))


@v1_router.get("/settings", summary="获取标注设置", response_model=ApiResponse)
def v1_get_annotation_settings(
    request: Request,
    principal: Caller,
    db: DbSession,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    setting = service.get_setting(app_id)
    if not setting:
        return success(
            data={
                "app_id": str(app_id),
                "workspace_id": str(principal.workspace_id),
                "similarity_threshold": 0.85,
                "model_config_id": None,
                "enabled": 0,
            }
        )
    return success(data=_setting_response(setting))


@v1_router.put("/settings", summary="更新标注设置", response_model=ApiResponse)
def v1_update_annotation_settings(
    request: Request,
    payload: AnnotationSettingUpdate,
    principal: Caller,
    db: DbSession,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    setting = service.update_setting(
        app_id=app_id,
        workspace_id=principal.workspace_id,
        similarity_threshold=payload.similarity_threshold,
        model_config_id=payload.model_config_id,
        enabled=payload.enabled,
    )
    return success(data=_setting_response(setting))


@v1_router.put("/{annotation_id}", summary="更新标注", response_model=ApiResponse)
def v1_update_annotation(
    request: Request,
    annotation_id: UUID,
    payload: AnnotationUpdate,
    principal: Caller,
    db: DbSession,
    model_invoke: ModelInvoke,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    setting = _require_v1_annotation_enabled(service, app_id)

    annotation = service.get_annotation(annotation_id)
    if not annotation or str(annotation.app_id) != str(app_id):
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)

    embedding = None
    if payload.question and setting.model_config_id:
        embedding = _generate_question_embedding(
            service,
            payload.question,
            model_config_id=setting.model_config_id,
            principal=principal,
            workspace_id=principal.workspace_id,
            client=model_invoke,
            failure_log="重新生成Embedding失败: %s",
        )

    annotation = service.update_annotation(
        annotation_id=annotation_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
    )
    return success(data=Annotation.model_validate(annotation))


@v1_router.delete("/{annotation_id}", summary="删除标注", response_model=ApiResponse)
def v1_delete_annotation(
    request: Request,
    annotation_id: UUID,
    principal: Caller,
    db: DbSession,
):
    app_id = _resolve_v1_app(request, db, principal)

    service = AnnotationService(db)
    annotation = service.get_annotation(annotation_id)
    if not annotation or str(annotation.app_id) != str(app_id):
        raise BusinessException(ANNOTATION_NOT_FOUND_MSG, BizCode.NOT_FOUND)

    service.delete_annotation(annotation_id)
    return success(msg="标注删除成功")


__all__ = ["router", "v1_router"]
