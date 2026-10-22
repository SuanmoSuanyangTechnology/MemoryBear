"""App family management routes.

Mirrors the monolith ``app_controller`` app CRUD + share + agent config
+ workflow draft read/write + publish / release / rollback + DSL/workflow
import/export + statistics + publish-as-tool endpoints (definition plane).
Guards outermost, quota second — the same evaluation order as the host
decorators (``@cur_workspace_access_guard`` above ``@check_app_quota``).
"""

from __future__ import annotations

import io
import logging
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...db import end_read_transaction
from ...errors import BizCode, BusinessException
from ...guards.workspace_access import require_workspace_access
from ...models.app_model import App, AppType
from ...models.document_model import Document
from ...models.file_model import File as FileModel
from ...quota.alerts import report_quota_change_sync
from ...quota.usage import ensure_app_quota, require_app_quota
from ...repositories import cross_domain_read
from ...schemas import app_schema, tool_schema, workflow_schema
from ...services.agent_config_helper import enrich_agent_config
from ...services.app_dsl_service import AppDslService
from ...services.app_service import AppService
from ...services.app_share_service import AppShareService
from ...services.app_statistics_service import AppStatisticsService
from ...services.file_storage_service import FileStorageService, get_file_storage_service
from ...services.tool_service import ToolService
from ...services.workflow_import_service import WorkflowImportService
from ...services.workflow_service import WorkflowService
from ...services.workflow_tool_publish_service import WorkflowToolPublishService
from ...storage.exceptions import StorageError
from ..dependencies import (
    Principal,
    SyncRedis,
    get_async_db,
    get_principal,
    get_sync_db,
    get_sync_redis,
)
from ..schemas.common import fail, success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/apps", tags=["Apps"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
AppQuota = Annotated[None, Depends(require_app_quota)]
CacheClient = Annotated[SyncRedis, Depends(get_sync_redis)]


@router.post("", summary="创建应用（可选创建 Agent 配置）", response_model=ApiResponse)
def create_app(
        payload: app_schema.AppCreate,
        workspace_id: WorkspaceId,
        _quota: AppQuota,
        db: DbSession,
        principal: Caller,
):
    service = AppService(db)
    app = service.create_app(
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        tenant_id=principal.tenant_id,
        data=payload,
    )
    # After creation, fetch and return the bound ontology info (no ontology_id -> default)
    return success(data=service.to_schema_with_ontology(app, workspace_id))


@router.get("", summary="应用列表（分页）", response_model=ApiResponse)
def list_apps(
        workspace_id: WorkspaceId,
        db: DbSession,
        type: str | None = Query(
            None, description="应用类型过滤，支持逗号分隔多值，如 'agent,multi_agent'"
        ),
        visibility: str | None = None,
        status: str | None = None,
        search: str | None = None,
        tag_search: str | None = None,
        include_shared: bool = True,
        shared_only: bool = False,
        page: int = 1,
        pagesize: int = 10,
        ids: str | None = None,
        orchestration_mode: str | None = Query(
            None, description="编排模式过滤（仅 multi_agent 生效），如 supervisor_loop"
        ),
        ontology_status: Annotated[
            Literal["all", "bound", "default"],
            Query(description="本体状态筛选：all 全部 / bound 已关联本体 / default 仅默认本体"),
        ] = "all",
        field_search: Annotated[
            str | None,
            Query(description="标签模式搜索：模糊匹配绑定本体的场景自定义字段展示名"),
        ] = None,
):
    """List apps

    - Includes the workspace's own apps plus apps shared into it by default
    - Set include_shared=false to see only the workspace's own apps
    - When ids is provided, comma-separated apps are fetched without pagination
    - search supports: fuzzy app-name search and exact API Key search
    - tag_search supports: fuzzy app-tag search
    - ontology_status / field_search: ontology filters (enterprise only; not applied
      to ids / exact API Key lookups)
    - Each app carries an ontology sub-object (current ontology; null in community)
    """
    service = AppService(db)

    # Search via the search param: fuzzy app-name match and exact API Key match
    if search:
        search = search.strip()
        # Try an exact API Key match (API Keys are usually long)
        if len(search) >= 10:
            matched_id = cross_domain_read.get_api_key_resource_id(db, workspace_id, search)
            if matched_id:
                # API Key matched: return the apps bound to it directly
                ids = str(matched_id)

    # When ids is present, fetch the apps by ids (no pagination)
    if ids is not None:
        app_ids = [app_id.strip() for app_id in ids.split(',') if app_id.strip()]
        if app_ids:
            items_orm = service.get_apps_by_ids(app_ids, workspace_id)
            items = service.to_schemas_with_ontology(items_orm, workspace_id)
            # Return the standard paginated shape
            meta = PageMeta(page=1, pagesize=len(items), total=len(items), hasnext=False)
            return success(data=PageData(page=meta, items=items))
        # Empty ids: return an empty list
        meta = PageMeta(page=1, pagesize=0, total=0, hasnext=False)
        return success(data=PageData(page=meta, items=[]))

    # Regular paginated query
    items_orm, total = service.list_apps(
        workspace_id=workspace_id,
        type=type,
        visibility=visibility,
        status=status,
        search=search,
        tag_search=tag_search,
        include_shared=include_shared,
        shared_only=shared_only,
        page=page,
        pagesize=pagesize,
        ontology_status=ontology_status,
        field_search=field_search,
        orchestration_mode=orchestration_mode,
    )

    items = service.to_schemas_with_ontology(items_orm, workspace_id)
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))


@router.get(
    "/my-shared-out",
    summary="列出本工作空间主动分享出去的记录",
    response_model=ApiResponse,
)
def list_my_shared_out(
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """List all records this workspace has actively shared out to other workspaces (my shares)"""
    service = AppShareService(db)
    shares = service.list_my_shared_out(workspace_id=workspace_id)
    return success(data=service.share_schemas_with_ontology(shares))


@router.delete(
    "/share/{target_workspace_id}",
    summary="取消对某工作空间的所有应用分享",
    response_model=ApiResponse,
)
def unshare_all_apps_to_workspace(
        target_workspace_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Cancel all app shares from current workspace to a target workspace."""
    service = AppShareService(db)
    count = service.unshare_all_apps_to_workspace(
        target_workspace_id=target_workspace_id,
        workspace_id=workspace_id
    )
    return success(msg=f"已取消 {count} 个应用的分享", data={"count": count})


@router.get("/{app_id}", summary="获取应用详情", response_model=ApiResponse)
def get_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Get app details

    - Supports the workspace's own apps
    - Supports apps shared into the workspace
    """
    service = AppService(db)
    app = service.get_app(app_id, workspace_id)

    # Convert to the schema and set the is_shared field
    app_schema_obj = service._convert_to_schema(app, workspace_id)
    return success(data=app_schema_obj)


@router.put("/{app_id}", summary="更新应用基本信息", response_model=ApiResponse)
def update_app(
        app_id: UUID,
        payload: app_schema.AppUpdate,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service.update_app(app_id=app_id, data=payload, workspace_id=workspace_id)
    return success(data=app_schema.App.model_validate(app))


@router.delete("/{app_id}", summary="删除应用", response_model=ApiResponse)
def delete_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    """Delete an app

    Cascades to:
    - Agent config
    - Releases
    - Conversations and messages
    """
    logger.info(
        "用户请求删除应用",
        extra={
            "app_id": str(app_id),
            "user_id": str(principal.actor_id),
            "workspace_id": str(workspace_id)
        }
    )

    service = AppService(db)
    service.delete_app(app_id=app_id, workspace_id=workspace_id)

    return success(msg="应用删除成功")


@router.post("/{app_id}/copy", summary="复制应用", response_model=ApiResponse)
def copy_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        _quota: AppQuota,
        db: DbSession,
        principal: Caller,
        new_name: str | None = None,
        payload: app_schema.CopyAppRequest = None,
):
    """Copy an app (basic info and config included)

    - Copies the app's basic info (name, description, icon, etc.)
    - Copies the Agent config (for the agent type)
    - The new app starts as a draft
    - The source app is not affected
    """
    # body takes precedence over query param for backward compatibility
    new_name = (payload.new_name if payload else None) or new_name
    logger.info(
        "用户请求复制应用",
        extra={
            "source_app_id": str(app_id),
            "user_id": str(principal.actor_id),
            "workspace_id": str(workspace_id),
            "new_name": new_name
        }
    )

    service = AppService(db)
    new_app = service.copy_app(
        app_id=app_id,
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        new_name=new_name
    )

    return success(data=app_schema.App.model_validate(new_app), msg="应用复制成功")


@router.put("/{app_id}/config", summary="更新 Agent 配置", response_model=ApiResponse)
def update_agent_config(
        app_id: UUID,
        payload: app_schema.AgentConfigUpdate,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    cfg = service.update_agent_config(app_id=app_id, data=payload, workspace_id=workspace_id)
    cfg = enrich_agent_config(cfg)
    return success(data=app_schema.AgentConfig.model_validate(cfg))


@router.get(
    "/{app_id}/model/parameters/default",
    summary="获取 Agent 模型参数默认配置",
    response_model=ApiResponse,
)
def get_agent_model_parameters(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    model_parameters = service.get_default_model_parameters(app_id=app_id)
    return success(data=model_parameters, msg="获取 Agent 模型参数默认配置")


@router.get("/{app_id}/config", summary="获取 Agent 配置", response_model=ApiResponse)
def get_agent_config(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    cfg = service.get_agent_config(app_id=app_id, workspace_id=workspace_id)
    # The config always exists (a default template is returned when missing)
    cfg = enrich_agent_config(cfg)
    return success(data=app_schema.AgentConfig.model_validate(cfg))


@router.get("/{app_id}/opening", summary="获取应用开场白配置", response_model=ApiResponse)
def get_opening(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Return the opening statement text and suggested questions for chat UI init"""
    service = AppService(db)

    # Fetch features according to the app type
    app = db.get(App, app_id)
    if app and app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        cfg = service.get_workflow_config(app_id=app_id, workspace_id=workspace_id)
        features = cfg.features or {}
    else:
        cfg = service.get_agent_config(app_id=app_id, workspace_id=workspace_id)
        features = cfg.features or {}
        if hasattr(features, "model_dump"):
            features = features.model_dump()

    opening = features.get("opening_statement", {})
    return success(data=app_schema.OpeningResponse(
        enabled=opening.get("enabled", False),
        statement=opening.get("statement"),
        suggested_questions=opening.get("suggested_questions", []),
    ))


@router.get("/{app_id}/workflow", response_model=ApiResponse)
def get_workflow_config(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    cfg = service.get_workflow_config(app_id=app_id, workspace_id=workspace_id)
    # The config always exists (a default template is returned when missing)
    return success(data=workflow_schema.WorkflowConfig.model_validate(cfg))


@router.put("/{app_id}/workflow", summary="更新 Workflow 配置", response_model=ApiResponse)
def update_workflow_config(
        app_id: UUID,
        payload: workflow_schema.WorkflowConfigUpdate,
        workspace_id: WorkspaceId,
        db: DbSession,
        cache: CacheClient,
):
    service = AppService(db)
    if payload.variables:
        resolved = WorkflowService(db)._resolve_variables_file_defaults_sync(
            [v.model_dump() for v in payload.variables]
        )
        # Patch default values back into VariableDefinition objects
        for var_def, resolved_def in zip(payload.variables, resolved, strict=True):
            var_def.default = resolved_def.get("default", var_def.default)
    cfg = service.update_workflow_config(
        app_id=app_id, data=payload, workspace_id=workspace_id, redis_client=cache
    )
    return success(data=workflow_schema.WorkflowConfig.model_validate(cfg))


@router.post("/{app_id}/publish", summary="发布应用（生成不可变快照）", response_model=ApiResponse)
def publish_app(
        app_id: UUID,
        payload: app_schema.PublishRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    service = AppService(db)
    release = service.publish(
        app_id=app_id,
        publisher_id=principal.actor_id,
        workspace_id=workspace_id,
        version_name=payload.version_name,
        release_notes=payload.release_notes,
        name=payload.name,
        icon=payload.icon,
    )
    return success(data=service.release_schema(release))


@router.get(
    "/{app_id}/release/display_info",
    summary="获取发布展示信息",
    response_model=ApiResponse,
)
def get_release_display_info(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Default display info before publish: release.name/icon when a current
    release exists, else app.name/icon"""
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    release = service.get_current_release(app_id=app_id, workspace_id=workspace_id)
    if release:
        return success(data=app_schema.AppDisplayInfo(name=release.name, icon=release.icon))
    return success(data=app_schema.AppDisplayInfo(name=app.name, icon=app.icon))


@router.get("/{app_id}/release", summary="获取当前发布版本", response_model=ApiResponse)
def get_current_release(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    release = service.get_current_release(app_id=app_id, workspace_id=workspace_id)
    if not release:
        return success(data=None)
    return success(data=service.release_schema(release))


@router.get("/{app_id}/releases", summary="列出历史发布版本（倒序）", response_model=ApiResponse)
def list_releases(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    releases = service.list_releases(app_id=app_id, workspace_id=workspace_id)
    return success(data=service.release_schemas(releases))


@router.post("/{app_id}/rollback/{version}", summary="回滚到指定版本", response_model=ApiResponse)
def rollback(
        app_id: UUID,
        version: int,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    release = service.rollback(app_id=app_id, version=version, workspace_id=workspace_id)
    return success(data=service.release_schema(release))


@router.post("/{app_id}/share", summary="分享应用到其他工作空间", response_model=ApiResponse)
def share_app(
        app_id: UUID,
        payload: app_schema.AppShareCreate,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    """Share an app to other workspaces

    - Only apps of the caller's own workspace can be shared
    - Cannot share to the caller's own workspace
    - The same app cannot be shared to the same workspace twice
    """
    service = AppShareService(db)
    shares = service.share_app(
        app_id=app_id,
        target_workspace_ids=payload.target_workspace_ids,
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        permission=payload.permission
    )

    data = service.share_schemas(shares)
    return success(data=data, msg=f"应用已分享到 {len(shares)} 个工作空间")


@router.delete(
    "/{app_id}/share/{target_workspace_id}",
    summary="取消应用分享",
    response_model=ApiResponse,
)
def unshare_app(
        app_id: UUID,
        target_workspace_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Cancel an app share

    - Only shares of the caller's own workspace apps can be cancelled
    """
    service = AppShareService(db)
    service.unshare_app(
        app_id=app_id,
        target_workspace_id=target_workspace_id,
        workspace_id=workspace_id
    )

    return success(msg="应用分享已取消")


@router.patch(
    "/{app_id}/share/{target_workspace_id}",
    summary="更新共享权限",
    response_model=ApiResponse,
)
def update_share_permission(
        app_id: UUID,
        target_workspace_id: UUID,
        payload: app_schema.UpdateSharePermissionRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Update the share permission (readonly <-> editable)

    - Only shares of the caller's own workspace apps can be modified
    """
    service = AppShareService(db)
    share = service.update_share_permission(
        app_id=app_id,
        target_workspace_id=target_workspace_id,
        permission=payload.permission,
        workspace_id=workspace_id
    )

    return success(data=service.share_schemas([share])[0])


@router.get("/{app_id}/shares", summary="列出应用的分享记录", response_model=ApiResponse)
def list_app_shares(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """List all share records of an app

    - Only share records of the caller's own workspace apps can be viewed
    """
    service = AppShareService(db)
    shares = service.list_app_shares(
        app_id=app_id,
        workspace_id=workspace_id
    )

    data = service.share_schemas(shares)
    return success(data=data)


@router.delete(
    "/shared/{source_workspace_id}",
    summary="批量移除某来源工作空间的所有共享应用",
    response_model=ApiResponse,
)
def remove_all_shared_apps_from_workspace(
        source_workspace_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Remove all shared apps from a specific source workspace (recipient operation)."""
    service = AppShareService(db)
    count = service.remove_all_shared_apps_from_workspace(
        source_workspace_id=source_workspace_id,
        workspace_id=workspace_id
    )
    return success(msg=f"已移除 {count} 个共享应用", data={"count": count})


@router.delete("/{app_id}/shared", summary="移除共享给我的应用", response_model=ApiResponse)
def remove_shared_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """The recipient removes a shared app from its own workspace

    - The source app is not deleted, only the share record
    - Only apps shared into the caller's own workspace can be removed
    """
    service = AppShareService(db)
    service.remove_shared_app(
        app_id=app_id,
        workspace_id=workspace_id
    )

    return success(msg="已移除共享应用")


# ==================== Statistics / publish as tool ====================


@router.get("/{app_id}/statistics", summary="应用统计数据", response_model=ApiResponse)
def get_app_statistics(
        app_id: UUID,
        start_date: int,
        end_date: int,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    """Get app statistics

    Args:
        app_id: App ID
        start_date: Start timestamp (milliseconds)
        end_date: End timestamp (milliseconds)
        db: Database session
        current_user: Current user

    Returns:
        - daily_conversations: Daily conversation counts
        - total_conversations: Total conversations
        - daily_new_users: Daily new-user counts
        - total_new_users: Total new users
        - daily_api_calls: Daily API-call counts
        - total_api_calls: Total API calls
        - daily_tokens: Daily token consumption
        - total_tokens: Total token consumption
    """
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    stats_service = AppStatisticsService(db)

    result = stats_service.get_app_statistics(
        app_id=app_id,
        workspace_id=workspace_id,
        tenant_id=principal.tenant_id,
        start_date=start_date,
        end_date=end_date
    )

    return success(data=result)


@router.get("/workspace/api-statistics", summary="工作空间API调用统计", response_model=ApiResponse)
def get_workspace_api_statistics(
        start_date: int,
        end_date: int,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Get workspace API-call statistics

    Args:
        start_date: Start timestamp (milliseconds)
        end_date: End timestamp (milliseconds)
        db: Database session
        current_user: Current user

    Returns:
        List of daily statistics, each item containing:
        - date: Date
        - total_calls: Total calls of the day
        - app_calls: App calls of the day
        - service_calls: Service calls of the day
    """
    stats_service = AppStatisticsService(db)

    result = stats_service.get_workspace_api_statistics(
        workspace_id=workspace_id,
        start_date=start_date,
        end_date=end_date
    )

    return success(data=result)


@router.post("/{app_id}/publish_tool", summary="发布工作流为工具", response_model=ApiResponse)
def publish_workflow_as_tool(
        app_id: UUID,
        payload: tool_schema.WorkflowToolCreateRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    service = WorkflowToolPublishService(db)
    tool_config = service.publish_workflow_as_tool(
        workflow_app_id=app_id,
        tool_name=payload.name,
        tool_description=payload.description or "",
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        icon=payload.icon,
        timeout=payload.timeout,
        tags=payload.tags,
        release_id=UUID(payload.release_id) if payload.release_id else None,
    )

    detail = ToolService(db).get_tool_detail(str(tool_config.id), principal.tenant_id)
    return success(data=detail or tool_schema.ToolDetailSchema.model_validate(tool_config))


@router.get(
    "/{app_id}/publish_tool/preview",
    summary="预览工作流发布为工具的入参和出参",
    response_model=ApiResponse,
)
def preview_workflow_tool_publish(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        release_id: Annotated[
            UUID | None, Query(description="指定工作流发布版本ID，不传则使用当前发布版本")
        ] = None,
):
    preview = WorkflowToolPublishService(db).get_publish_preview(
        workflow_app_id=app_id,
        workspace_id=workspace_id,
        release_id=release_id,
    )
    return success(data=preview)


# ==================== DSL / workflow import / export ====================


@router.get("/{app_id}/workflow/export", response_model=ApiResponse)
def export_workflow_config(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Export the workflow config as a YAML file"""
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    workflow_service = WorkflowService(db)
    return success(data={"content": workflow_service.export_workflow_dsl(app_id=app_id)})


@router.post("/workflow/import", response_model=ApiResponse)
def import_workflow_config(
        workspace_id: WorkspaceId,
        db: DbSession,
        cache: CacheClient,
        file: Annotated[UploadFile, File()],
        platform: Annotated[str, Form()],
        app_id: Annotated[str | None, Form()] = None,
):
    """Import a workflow config from YAML content"""
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(code=BizCode.BAD_REQUEST.value, msg="Only yaml file is allowed")

    raw_text = file.file.read().decode("utf-8")
    import_service = WorkflowImportService(db)
    config = yaml.safe_load(raw_text)
    result = import_service.upload_config(platform=platform, config=config, redis_client=cache)
    return success(data=result)


@router.post("/workflow/import/save", response_model=ApiResponse)
def save_workflow_import(
        payload: workflow_schema.WorkflowImportSave,
        workspace_id: WorkspaceId,
        _quota: AppQuota,
        db: DbSession,
        principal: Caller,
        cache: CacheClient,
):
    import_service = WorkflowImportService(db)
    app = import_service.save_workflow(
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        temp_id=payload.temp_id,
        name=payload.name,
        description=payload.description,
        redis_client=cache,
    )
    return success(data=app_schema.App.model_validate(app))


@router.get("/{app_id}/export", summary="导出应用配置为 YAML 文件")
def export_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        release_id: UUID | None = None,
):
    """Export agent / multi_agent / workflow app config as a YAML file stream.
    release_id: release ID to export; when omitted, the current draft config is exported.
    """
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    yaml_str, filename = AppDslService(db).export_dsl(app_id, release_id)
    encoded = quote(filename, safe=".")
    yaml_bytes = yaml_str.encode("utf-8")
    file_stream = io.BytesIO(yaml_bytes)
    file_stream.seek(0)
    return StreamingResponse(
        file_stream,
        media_type="application/octet-stream; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
                 "Content-Length": str(len(yaml_bytes))}
    )


@router.post("/import", summary="从 YAML 文件导入应用", response_model=ApiResponse)
def import_app(
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
        cache: CacheClient,
        file: Annotated[UploadFile, File()],
        app_id: Annotated[str | None, Form()] = None,
):
    """Import an agent / multi_agent / workflow app from a YAML file.
    When app_id is passed, the app's config is overwritten (types must match);
    otherwise a new app is created.
    On cross-workspace / cross-tenant import, resources are matched by name:
    unmatched models / tools / knowledge bases are blanked; unmatched sub-agent /
    routing entries are dropped outright. All of them are reported in warnings.
    """
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(code=BizCode.BAD_REQUEST.value, msg="仅支持 YAML 文件")

    raw = file.file.read().decode("utf-8")
    dsl = yaml.safe_load(raw)
    if not dsl or "app" not in dsl:
        return fail(code=BizCode.BAD_REQUEST.value, msg="YAML 格式无效，缺少 app 字段")

    target_app_id = UUID(app_id) if app_id else None
    # Check quota only when creating a new app; overwriting an existing app skips it
    if target_app_id is None:
        ensure_app_quota(db, principal)
    result_app, warnings = AppDslService(db).import_dsl(
        dsl=dsl,
        workspace_id=workspace_id,
        tenant_id=principal.tenant_id,
        user_id=principal.actor_id,
        app_id=target_app_id,
        redis_client=cache,
    )
    # import_dsl has committed the new app on return; re-evaluate the quota alert
    # from the authoritative count.
    if target_app_id is None:
        report_quota_change_sync(
            principal.tenant_id,
            "app_quota",
            workspace_id,
        )
    return success(
        data={"app": app_schema.App.model_validate(result_app), "warnings": warnings},
        msg="应用导入成功" + ("，但部分资源需手动配置" if warnings else "")
    )


@router.get("/citations/{document_id}/download", summary="下载引用文档原始文件")
async def download_citation_file(
        document_id: Annotated[UUID, Path(description="引用文档ID")],
        db: Annotated[AsyncSession, Depends(get_async_db)],
        storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Stream the original file of a cited document.

    Host parity: no permission check here — the document UUID is the capability,
    and the console only renders the link when the app enables
    ``citation.allow_download``.
    """
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        raise BusinessException("文档不存在", BizCode.NOT_FOUND)

    file_record = (
        await db.execute(select(FileModel).where(FileModel.id == doc.file_id))
    ).scalar_one_or_none()
    if file_record is None:
        raise BusinessException("原始文件不存在", BizCode.NOT_FOUND)

    # Release the pooled connection before the storage read: a large download
    # must not hold an idle-in-transaction connection.
    await end_read_transaction(db, "apps.citation_download")

    try:
        content = await storage_service.download_file(file_record.file_key)
    except (FileNotFoundError, StorageError) as exc:
        logger.error(f"Storage download failed: {exc}")
        raise BusinessException("文件未找到", BizCode.NOT_FOUND) from exc

    encoded_name = quote(doc.file_name)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


__all__ = ["router"]
