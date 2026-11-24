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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...models.app_model import AppType
from ...schemas import app_schema
from ...services.asset_service import AssetService
from ...services.category_service import CategoryService
from ...utils.datetime_utils import to_timestamp_ms
from ..dependencies import API_KEY_ID_HEADER, Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/apps/assets", tags=["Resource Center"])

# The `/v1` face of the same four read/install handlers. The catalog lives under
# `/apps/assets` internally because the console calls it there, but the v1
# contract puts it next to `/v1/apps`, `/v1/skills` and `/v1/tools` (§3.4 of the
# design doc). Two paths, one implementation: the handlers below are decorated
# twice, so the catalog logic never forks.
#
# Only these four are re-declared. `PUT`/`DELETE /{asset_id}` (share-mode edit
# and unshare) stay on the internal path: they belong to the publisher's 「我的
# 共享」 and are not part of the v1 asset contract, so this alias must not carry
# them — a prefix alias would otherwise expose a write surface the scope was
# never meant to grant.
assets_v1_router = APIRouter(prefix="/assets", tags=["Resource Center"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.get("", summary="资源中心：租户内浏览应用", response_model=ApiResponse)
@assets_v1_router.get("", summary="资源中心：租户内浏览应用", response_model=ApiResponse)
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
    # Same gate as `GET /v1/apps`: an unknown type is a caller error, not an
    # empty catalog. Without this the value reached the SQL string comparison
    # and answered 200 + empty list, which is indistinguishable from "nothing
    # shared yet".
    if type and type not in {t.value for t in AppType}:
        raise RequestValidationError(
            [{"loc": ("query", "type"),
              "msg": f"非法的应用类型: {type}",
              "type": "enum"}]
        )

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
@assets_v1_router.get("/categories", summary="资源中心：功能分类字典", response_model=ApiResponse)
def list_categories(
        db: DbSession,
        resource_type: str = Query(
            "app", description="app | tool | skill_package，默认取 app 组"
        ),
):
    """分类字典，只返回分类本身，不统计数量。

    字段与市场侧 `GET /api/market/categories` 的 `items[]` 同名同义
    （``code`` / ``name`` / ``name_en`` / ``sort``），少了 ``id`` /
    ``resource_type`` / ``enabled`` / ``count``。前端同一套渲染代码取两边的
    分类栏，切语种用 ``name`` 或 ``name_en``，顺序一律取 ``sort``。

    取值来自 core 自己的 `app_categories` 表，不再是代码里的常量：加一个分类调
    `POST /internal/v1/app-categories` 即可，不用重发服务。

    **市场侧是另一套存储**（`market_categories`，企业端才装）。两者靠 ``code``
    对齐、各自 seed 一份，所以这里不能假定市场上有什么分类。

    没有 ``count``，也不按行返回 ``id``：一期只有 ``app`` 组有消费方，
    ``resource_type`` 默认 ``app``；传 ``tool`` / ``skill_package`` 也能取到，
    只是还没有消费方。取值越界直接报错（复用 ``CategoryService`` 的校验，
    错误码 ``VALIDATION_FAILED``），不返回空列表冒充成功。
    """

    resolved = CategoryService._validate_types(resource_type)[0]
    return success(
        data=CategoryService(db).list_options(resource_type=resolved),
        msg="获取功能分类成功",
    )


@router.get("/{asset_id}", summary="资源中心：应用资源详情", response_model=ApiResponse)
@assets_v1_router.get("/{asset_id}", summary="资源中心：应用资源详情", response_model=ApiResponse)
def get_asset(asset_id: UUID, db: DbSession, principal: Caller):
    """Detail page for one entry (same component as the market detail page).

    Opening it increments ``view_count`` — that is the only write this endpoint
    performs, and it is why the counter is separate from ``install_count``.
    """
    service = AssetService(db)
    return success(data=service.get_asset(asset_id=asset_id, tenant_id=principal.tenant_id))


@router.post("/{asset_id}/install", summary="资源中心：添加到我的空间", response_model=ApiResponse)
@assets_v1_router.post(
    "/{asset_id}/install", summary="资源中心：添加到我的空间", response_model=ApiResponse
)
def install_asset(
        asset_id: UUID,
        payload: app_schema.AssetInstallRequest,
        http_request: Request,
        db: DbSession,
        principal: Caller,
):
    """Pull the entry into a space.

    The caller does **not** choose read-only vs copy: the entry's ``share_mode``
    alone decides (``readonly`` → ``reference``, ``editable`` → ``copy``), so a
    consumer can never escalate its own permission.

    **Two faces, two target rules.** The console (JWT face) lets the user pick
    the target from a workspace picker, so the body field stays required there.
    A v1 API Key is bound to exactly one workspace and the v1 catalog contract is
    "install into my own space", so on the API-key face the field is optional:
    omitted → the Key's workspace; supplied but different → rejected rather than
    silently ignored. Silently landing an install in a space the caller did not
    name is the failure mode this branch exists to prevent (the same reason
    ``/v1/apps`` never honours a ``workspace_id`` query).
    """
    api_key_face = http_request.headers.get(API_KEY_ID_HEADER) is not None
    target_workspace_id = payload.target_workspace_id

    if api_key_face:
        if target_workspace_id is None:
            target_workspace_id = principal.workspace_id
        elif target_workspace_id != principal.workspace_id:
            raise BusinessException(
                "API Key 只能把资源添加到它绑定的空间，target_workspace_id 可省略",
                BizCode.WORKSPACE_ACCESS_DENIED,
                data={
                    "reason": "INSTALL_TARGET_NOT_KEY_WORKSPACE",
                    "key_workspace_id": str(principal.workspace_id),
                },
            )
    elif target_workspace_id is None:
        raise BusinessException(
            "target_workspace_id 不能为空", BizCode.MISSING_PARAMETER
        )

    if target_workspace_id is None:
        # Only reachable when the Key carries no workspace at all; the guard
        # already rejects that, so this is a defensive assertion.
        raise BusinessException("缺少目标空间", BizCode.MISSING_PARAMETER)

    service = AssetService(db)
    result = service.install_asset(
        asset_id=asset_id,
        tenant_id=principal.tenant_id,
        user_id=principal.actor_id,
        target_workspace_id=target_workspace_id,
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