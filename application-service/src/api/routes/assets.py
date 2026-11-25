"""Tenant resource center routes (design doc §6.1).

Four entry operations + one read-only dictionary. The catalog is not an admin
console: an entry is visible because someone shared it, and it disappears when
they cancel. There is deliberately no publish/unpublish endpoint, no
version-forward endpoint, and no sync / my-installs / preview endpoint (see the
module docstring in ``services/asset_service.py`` for why each is excluded).

The dictionary endpoint exists because the publish dialog and the catalog filter
bar need the 8 categories, and having the frontend hardcode them meant a frontend
release every time the list changes. It returns **no counts** — counts are the
market's business (`/api/market/categories`), this one is just the enum.

Mounted at ``/apps/assets`` and registered **before** ``apps_router`` so that
``/apps/{app_id}`` cannot swallow ``/apps/assets``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...schemas import app_schema
from ...services.asset_service import AssetService
from ...services.category_service import CategoryService
from ...utils.datetime_utils import to_timestamp_ms
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/apps/assets", tags=["Resource Center"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.get("", summary="资源中心：租户内浏览应用", response_model=ApiResponse)
def list_assets(
        db: DbSession,
        principal: Caller,
        category: str | None = Query(
            None, description="功能分类（单值，命中分类数组任一元素即匹配）"
        ),
        q: str | None = Query(None, description="搜索：应用名称 / 描述 / 发布人名称"),
        type: str | None = Query(None, description="应用类型：agent | multi_agent | workflow"),
        page: int = Query(1, ge=1, description="页码"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
):
    """List published entries of the caller's tenant.

    Every row is a snapshot of the pinned release, so a source edit or a newer
    release never changes what consumers see until the sharer forwards the
    version.
    """
    service = AssetService(db)
    items, total = service.list_assets(
        tenant_id=principal.tenant_id,
        category=category,
        q=q,
        app_type=type,
        page=page,
        pagesize=pagesize,
    )
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))


# 必须排在 ``/{asset_id}`` 之前：``asset_id`` 是 UUID 类型参数，先注册的话
# ``/categories`` 会被它先匹配，直接返回 422（"categories" 不是合法 UUID）。
@router.get("/categories", summary="资源中心：功能分类字典", response_model=ApiResponse)
def list_categories(db: DbSession):
    """分类字典，只返回分类本身，不统计数量。

    字段与市场侧 `GET /api/market/categories` 的 `items[]` 同名同义
    （``code`` / ``name`` / ``name_en`` / ``sort``），少了 ``id`` /
    ``resource_type`` / ``enabled`` / ``count``。前端同一套渲染代码取两边的
    分类栏，切语种用 ``name`` 或 ``name_en``，顺序一律取 ``sort``。

    取值来自 core 自己的 `app_categories` 表，不再是代码里的常量：加一个分类调
    `POST /internal/v1/app-categories` 即可，不用重发服务。

    **市场侧是另一套存储**（`market_categories`，企业端才装）。两者靠 ``code``
    对齐、各自 seed 一份，所以这里不能假定市场上有什么分类。

    没有 ``resource_type`` 参数：一期只有 ``app`` 组有消费方。
    """

    return success(
        data=CategoryService(db).list_options(resource_type="app"),
        msg="获取功能分类成功",
    )


@router.get("/{asset_id}", summary="资源中心：应用资源详情", response_model=ApiResponse)
def get_asset(asset_id: UUID, db: DbSession, principal: Caller):
    """Detail page for one entry (same component as the market detail page).

    Opening it increments ``view_count`` — that is the only write this endpoint
    performs, and it is why the counter is separate from ``install_count``.
    """
    service = AssetService(db)
    return success(data=service.get_asset(asset_id=asset_id, tenant_id=principal.tenant_id))


@router.post("/{asset_id}/install", summary="资源中心：添加到我的空间", response_model=ApiResponse)
def install_asset(
        asset_id: UUID,
        payload: app_schema.AssetInstallRequest,
        db: DbSession,
        principal: Caller,
):
    """Pull the entry into a space.

    The caller does **not** choose read-only vs copy: the entry's ``share_mode``
    alone decides (``readonly`` → ``reference``, ``editable`` → ``copy``), so a
    consumer can never escalate its own permission.
    """
    service = AssetService(db)
    result = service.install_asset(
        asset_id=asset_id,
        tenant_id=principal.tenant_id,
        user_id=principal.actor_id,
        target_workspace_id=payload.target_workspace_id,
    )
    return success(data=result, msg="已添加到空间")


@router.put("/{asset_id}", summary="资源中心：更新共享权限", response_model=ApiResponse)
def update_asset_share_mode(
        asset_id: UUID,
        payload: app_schema.AssetShareModeRequest,
        db: DbSession,
        principal: Caller,
):
    """Switch one entry between ``readonly`` and ``editable``.

    This is the permission column of 「我的共享」. It is the **only** mutable
    share field: visibility still follows the share switches (share / unshare),
    and the pinned version still moves by re-sharing, so no publish or
    version-forward endpoint appears here.

    Only affects installs performed **after** the change — the form is read live
    from the entry, while existing installation rows keep the permission they
    were created with.
    """
    service = AssetService(db)
    asset = service.update_share_mode(
        asset_id=asset_id,
        tenant_id=principal.tenant_id,
        share_mode=payload.share_mode,
        user_id=principal.actor_id,
        workspace_id=principal.workspace_id,
    )
    return success(
        data={
            "id": str(asset.id),
            "share_mode": asset.share_mode,
            "updated_at": to_timestamp_ms(asset.updated_at),
        },
        msg="已更新共享权限",
    )


@router.delete("/{asset_id}", summary="资源中心：取消共享", response_model=ApiResponse)
def unshare_asset(asset_id: UUID, db: DbSession, principal: Caller):
    """Cancel sharing: the entry leaves the catalog.

    Reference installations lose access immediately; copies are unaffected
    because they already diverged from the source.
    """
    service = AssetService(db)
    asset = service.unshare_asset(
        asset_id=asset_id,
        user_id=principal.actor_id,
        workspace_id=principal.workspace_id,
        tenant_id=principal.tenant_id,
    )
    return success(data={"id": str(asset.id), "status": asset.status}, msg="已取消共享")