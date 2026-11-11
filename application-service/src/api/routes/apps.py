"""App family management routes.

Mirrors the monolith ``app_controller`` app CRUD + share + agent config
+ workflow draft read/write + publish / release / rollback + DSL/workflow
import/export + statistics + publish-as-tool endpoints (definition plane).
Guards outermost, quota second — the same evaluation order as the host
decorators (``@cur_workspace_access_guard`` above ``@check_app_quota``).
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...db import end_read_transaction
from ...errors import BizCode, BusinessException, ResourceNotFoundException
from ...guards.workspace_access import require_workspace_access
from ...models.app_model import App, AppType
from ...models.document_model import Document
from ...models.file_model import File as FileModel
from ...plugins import get_plugin
from ...protocols.market_gateway import MARKET_SUBMISSION_FACTORY
from ...quota.alerts import report_quota_change_sync
from ...quota.usage import ensure_app_quota, require_app_quota
from ...repositories import cross_domain_read
from ...runtime import ProcessRuntime
from ...schemas import app_schema, tool_schema, workflow_schema
from ...services.agent_config_helper import enrich_agent_config
from ...services.app_dsl_service import AppDslService
from ...services.app_service import AppService
from ...services.app_share_service import AppShareService
from ...services.app_statistics_service import AppStatisticsService
from ...services.asset_release_resolver import app_cache_key
from ...services.asset_service import AssetService
from ...services.external_status_service import apply_external_status
from ...services.file_storage_service import FileStorageService, get_file_storage_service
from ...services.my_shares_service import MySharesService
from ...services.tool_service import ToolService
from ...services.workflow_import_service import WorkflowImportService
from ...services.workflow_service import WorkflowService
from ...services.workflow_tool_publish_service import WorkflowToolPublishService
from ...storage.exceptions import StorageError
from ...utils.redis_cache import delete_json
from ..dependencies import (
    Principal,
    SyncRedis,
    get_async_db,
    get_principal,
    get_runtime,
    get_sync_db,
    get_sync_redis,
)
from ..schemas.common import fail, success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/apps", tags=["Apps"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
AppQuota = Annotated[None, Depends(require_app_quota)]
CacheClient = Annotated[SyncRedis, Depends(get_sync_redis)]
# 市场网关在当前进程的 runtime 上（`build_market_gateway`），外部状态回填要用它。
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]


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
    return success(data=service.to_schema_with_ontology(app, workspace_id), msg="创建应用成功")


@router.get("", summary="应用列表（分页）", response_model=ApiResponse)
def list_apps(
        workspace_id: WorkspaceId,
        db: DbSession,
        cache: CacheClient,
        runtime: RuntimeDep,
        type: str | None = Query(
            None, description="应用类型过滤，支持逗号分隔多值，如 'agent,multi_agent'"
        ),
        visibility: str | None = None,
        status: str | None = None,
        search: str | None = None,
        tag_search: str | None = None,
        include_shared: bool = True,
        shared_only: bool = False,
        page: int = Query(1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量，最大 100"),
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
        include_external_status: Annotated[
            bool,
            Query(description="是否批量回填 external.listing_status / delist_actor；失败为 null"),
        ] = False,
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
    # type accepts comma-separated values, but each must be a valid app type enum
    if type:
        valid_types = {t.value for t in AppType}
        invalid_types = [
            t.strip() for t in type.split(",")
            if t.strip() and t.strip() not in valid_types
        ]
        if invalid_types:
            raise RequestValidationError(
                [{"loc": ("query", "type"),
                  "msg": f"非法的应用类型: {','.join(invalid_types)}",
                  "type": "enum"}]
            )

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
            if include_external_status:
                apply_external_status(
                    items,
                    resource_type="app",
                    tenant_id=principal.tenant_id,
                    market=runtime.market_gateway,
                    cache=cache,
                )
            # Return the standard paginated shape
            meta = PageMeta(page=1, pagesize=len(items), total=len(items), hasnext=False)
            return success(data=PageData(page=meta, items=items), msg="获取应用列表成功")
        # Empty ids: return an empty list
        meta = PageMeta(page=1, pagesize=0, total=0, hasnext=False)
        return success(data=PageData(page=meta, items=[]), msg="获取应用列表成功")

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
    if include_external_status:
        apply_external_status(
            items,
            resource_type="app",
            tenant_id=principal.tenant_id,
            market=runtime.market_gateway,
            cache=cache,
        )
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items), msg="获取应用列表成功")


@router.get(
    "/my-shared-out",
    summary="列出本工作空间主动分享出去的记录",
    response_model=ApiResponse,
)
def list_my_shared_out(
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
        runtime: RuntimeDep,
        page: int = Query(1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(20, ge=1, le=100, description="每页数量，最大 100"),
):
    """分享方视角的「我的共享」：一行 = 一个「应用 × 共享范围」。

    与旧实现的差别（契约 `GET /apps/my-shared-out`，Apifox 427331831）：

    - 不再是每行一条 `app_shares`（那是「共享给哪个目标空间」的老模型），而是
      资源中心 / 社区市场两条**独立链路**各铺一行；同一应用出现两次是正常的。
    - `data` 从数组改为 `{items, page}`：行的条数不再等于共享关系数。
    - `total` 是**行数**，不是应用个数。

    市场状态按批查一次（`market_sync` 有行的应用才会问），失败整批降 `unknown`
    并照常返回列表——列表页可用性优先。
    """

    service = MySharesService(db)
    rows, total = service.list_rows(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        market=runtime.market_gateway,
        page=page,
        pagesize=pagesize,
    )
    # 本体回填放在序列化之后：远端 binder 会先结束事务（同 AppShareService 的注释）
    service.fill_ontology(rows)
    meta = PageMeta(
        page=page, pagesize=pagesize, total=total,
        hasnext=(page * pagesize) < total,
    )
    return success(data=PageData(page=meta, items=rows), msg="获取我的共享成功")


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
        principal: Caller,
        cache: CacheClient,
        runtime: RuntimeDep,
        include_external_status: Annotated[
            bool,
            Query(description="是否回填 external.listing_status / delist_actor；仅市场来源有意义"),
        ] = False,
):
    """Get app details

    - Supports the workspace's own apps
    - Supports apps shared into the workspace
    - include_external_status=true 时额外回填 external.listing_status / delist_actor
    """
    service = AppService(db)
    app = service.get_app(app_id, workspace_id)

    # Convert to the schema and set the is_shared field
    app_schema_obj = service._convert_to_schema(app, workspace_id)
    if include_external_status:
        apply_external_status(
            [app_schema_obj],
            resource_type="app",
            tenant_id=principal.tenant_id,
            market=runtime.market_gateway,
            cache=cache,
        )
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
    return success(data=app_schema.App.model_validate(app), msg="更新应用成功")


@router.delete("/{app_id}", summary="删除应用", response_model=ApiResponse)
def delete_app(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        runtime: RuntimeDep,
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
    external = False
    if runtime.settings.application_service_market_mode == "gateway":
        app = service.get_app(app_id, workspace_id)
        external = app.origin == "external"
    service.delete_app(app_id=app_id, workspace_id=workspace_id)
    if external:
        try:
            asyncio.run(
                runtime.market_gateway.deregister_installation(
                    "app",
                    str(app_id),
                    str(principal.tenant_id),
                    str(principal.actor_id),
                )
            )
        except Exception as exc:
            logger.warning("application deregistration failed: %s", exc)

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
    return success(data=app_schema.AgentConfig.model_validate(cfg), msg="更新 Agent 配置成功")


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
    return success(
        data=workflow_schema.WorkflowConfig.model_validate(cfg),
        msg="更新工作流配置成功",
    )


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
        usage_examples=payload.usage_examples,
        setup_steps=payload.setup_steps,
        categories=payload.categories,
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


@router.post(
    "/{app_id}/share",
    summary="共享应用（发布弹窗三开关）",
    response_model=ApiResponse,
)
def share_app(
        app_id: UUID,
        payload: app_schema.AppShareRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
        runtime: ProcessRuntime = Depends(get_runtime),
):
    """发布弹窗的共享入口：三个开关，一次确认（设计文档 §6.0）。

    - 开关 1/2 → 租户资源中心条目（`app_assets`）
    - 开关 3 → 跨租户社区市场投稿（独立于上面两个，可以只开它）

    **单接口不等于单事务**：本地部分（条目 + `market_sync` 行）原子提交，
    投稿的 HTTP 在提交之后才发——市场的超时上限是 10 秒，且他的契约明令
    「禁止在数据库事务内发起 HTTP」。市场不可用时返回 `pending_sync` 而不是
    报错：本地那半已经生效，可重试而不必让用户重填。
    """

    if not payload.resource_center.enabled and not payload.community_market.enabled:
        raise BusinessException(
            "至少需要打开一个共享范围",
            BizCode.INVALID_PARAMETER,
            data={"reason": "EMPTY_SHARE_SCOPE"},
        )

    asset_service = AssetService(db)
    result: dict[str, Any] = {
        "app_id": str(app_id),
        "release_id": None,
        "resource_center": None,
        "community_market": None,
    }

    # ── 开关 1/2：租户资源中心 ──
    asset = asset_service.share_to_catalog(
        app_id=app_id,
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        tenant_id=principal.tenant_id,
        release_id=payload.release_id,
        enabled=payload.resource_center.enabled,
        allow_editable=payload.resource_center.allow_editable,
    )
    if asset is not None:
        result["release_id"] = str(asset.published_release_id)
        result["resource_center"] = {
            "asset_id": str(asset.id),
            "published_version": asset.published_version,
            "share_mode": asset.share_mode,
            "status": asset.status,
        }

    # ── 开关 3：社区市场投稿 ──
    staged: dict[str, Any] | None = None
    if payload.community_market.enabled:
        # 投稿编排是社区市场业务，代码在企业扩展包内；开源版没有它，开关 3
        # 必须在这里就失败（不落任何本地行），而不是留下一半写完的 `market_sync`。
        submission_factory = get_plugin(MARKET_SUBMISSION_FACTORY)
        if submission_factory is None:
            raise BusinessException(
                "社区市场未在当前部署中启用",
                BizCode.FEATURE_NOT_AVAILABLE,
                data={"reason": "MARKET_DISABLED"},
            )
        if asset is None:
            # 只开开关 3：市场投稿需要一条 `app_assets` 行做外键载体，
            # 但它以 `unpublished` 落库，不在资源中心展示。
            asset, release = asset_service.ensure_market_carrier(
                app_id=app_id,
                user_id=principal.actor_id,
                workspace_id=workspace_id,
                tenant_id=principal.tenant_id,
                release_id=payload.release_id,
            )
            staged_release_id = release.id
        else:
            staged_release_id = asset.published_release_id
        result["release_id"] = str(staged_release_id)

        submission = submission_factory(db, market=runtime.market_gateway)
        prepared = submission.prepare(
            app_id=app_id,
            release_id=staged_release_id,
            tenant_id=principal.tenant_id,
        )
        sync = submission.stage_sync(
            asset_id=asset.id,
            tenant_id=principal.tenant_id,
            release_id=staged_release_id,
            content_hash=prepared["content_hash"],
        )
        staged = {
            "submission": submission,
            "sync": sync,
            "manifest": prepared["manifest"],
            "release_id": staged_release_id,
            # 依赖的独立条目（§7.5.1 双轨）：投稿时先投依赖、再投应用
            "dependencies": prepared.get("dependencies") or [],
        }

    # 本地部分到此为止，一次提交。运行时缓存保存的是解析后的 release，必须在
    # 条目 pin 前移后再失效，避免未提交事务被读到或提交失败时误删缓存。
    db.commit()
    if asset is not None:
        asset_service.invalidate_reference_runtime_caches(asset)

    if staged is not None:
        # 事务外发 HTTP（可重试；失败不改本地结果）
        result["community_market"] = staged["submission"].dispatch(
            sync=staged["sync"],
            manifest=staged["manifest"],
            app_id=app_id,
            release_id=staged["release_id"],
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            dependencies=staged["dependencies"],
        )

    return success(data=result, msg="共享已完成")


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
        principal: Caller,
):
    """Remove a legacy share or a catalog read-only reference from this workspace.

    The source app and resource-center entry remain untouched. A local editable
    copy is not a shared reference and must use the normal app delete operation.
    """
    legacy_removed = AppShareService(db).remove_shared_app(
        app_id=app_id,
        workspace_id=workspace_id,
        commit=False,
    )
    asset_service = AssetService(db)
    removed_asset = asset_service.remove_reference_installation(
        app_id=app_id,
        workspace_id=workspace_id,
        tenant_id=principal.tenant_id,
        commit=False,
    )
    if not legacy_removed and removed_asset is None:
        raise ResourceNotFoundException("共享应用", str(app_id))

    db.commit()
    if removed_asset is not None:
        delete_json(app_cache_key(removed_asset.source_app_id, workspace_id))

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
    return success(
        data=detail or tool_schema.ToolDetailSchema.model_validate(tool_config),
        msg="工作流发布为工具成功",
    )


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
    return success(data=preview, msg="获取工作流工具发布预览成功")


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
    return success(data=result, msg="工作流兼容性分析成功")


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
        include_cluster: bool = True,
):
    """Export agent / multi_agent / workflow app config as a YAML file stream.
    release_id: release ID to export; when omitted, the current draft config is exported.
    include_cluster: multi-agent apps only — also export every sub-agent's config
    under a top-level `cluster:` section (default on).
    """
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    yaml_str, filename = AppDslService(db).export_dsl(
        app_id, release_id, include_cluster=include_cluster
    )
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
    unmatched models / tools / knowledge bases are blanked. Sub-agent /
    routing entries are rebound by id only (never by name); a cluster package
    (`cluster:` section) also creates its members. All of it is reported in
    warnings.
    """
    if not file.filename.lower().endswith((".yaml", ".yml")):
        return fail(code=BizCode.BAD_REQUEST.value, msg="仅支持 YAML 文件")

    raw = file.file.read().decode("utf-8")
    dsl = yaml.safe_load(raw)
    if not dsl or "app" not in dsl:
        return fail(code=BizCode.BAD_REQUEST.value, msg="YAML 格式无效，缺少 app 字段")

    target_app_id = UUID(app_id) if app_id else None
    # A packaged cluster creates one app per member before the root lands. The
    # quota must account for them up front; members that will be reused (same app
    # id still present) do not consume a slot.
    new_member_count = (
        AppDslService(db).count_importable_cluster_members(dsl, workspace_id)
        if target_app_id is None
        else 0
    )
    # Check quota only when creating a new app; overwriting an existing app skips it
    if target_app_id is None:
        ensure_app_quota(db, principal, extra=new_member_count)

    cluster_members: list[dict[str, Any]] = []
    is_cluster = bool(dsl.get("cluster"))
    try:
        result_app, warnings = AppDslService(db).import_dsl(
            dsl=dsl,
            workspace_id=workspace_id,
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            app_id=target_app_id,
            redis_client=cache,
            # A cluster lands root + members; the route owns the transaction so a
            # failure halfway rolls every member back instead of leaving orphans.
            commit=not is_cluster,
            cluster_report=cluster_members,
        )
        if is_cluster:
            db.commit()
            db.refresh(result_app)
    except Exception:
        if is_cluster:
            db.rollback()
        raise
    # import_dsl has committed the new app on return; re-evaluate the quota alert
    # from the authoritative count.
    if target_app_id is None:
        report_quota_change_sync(
            principal.tenant_id,
            "app_quota",
            workspace_id,
        )
    return success(
        data={
            "app": app_schema.App.model_validate(result_app),
            "warnings": warnings,
            "cluster": cluster_members,
        },
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
