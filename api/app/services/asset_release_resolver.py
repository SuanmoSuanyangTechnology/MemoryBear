"""资源中心版本解析：单一入口。

社区版（`core/api` 单体）与拆分服务（`core/application-service`）共用同一套
规则，两边各有一份实现是因为运行形态不同（单体一个进程、拆分服务另一个 python
包），但 SQL 与判定必须一致。

改造后同一个 `app_id` 在不同空间解析到**不同** release：源空间读
`apps.current_release_id`，消费方读条目钉住的 `app_assets.published_release_id`。
任何继续直读 `app.current_release_id` 的读路径都会让消费方看到比审核时更新的
版本——这正是本节要收敛的唯一原因。

| 情况 | 解析结果 |
| --- | --- |
| 调用方自己的空间 | `apps.current_release_id`（行为不变） |
| active `reference` 安装 | `app_assets.published_release_id`（钉住版本） |
| active `copy` 安装 | 副本自己的 `current_release_id`（副本是本地 app，第一个分支已覆盖） |
| 无安装记录、但有历史 `app_shares` | `apps.current_release_id`（仅兼容期，§11.1 M3） |
| 什么都没有 | 抛 403 `3003 WORKSPACE_NO_ACCESS` |

两条容易漏的规则：

- `reference` 安装自条目变为 `unpublished` 起立即失效（取消共享）；副本不受影响。
- 缓存键必须带 workspace 维度。旧键 `cache:v2:app:{app_id}` 假设一个应用只有
  一个 release，改造后会把两个空间的钉住版本串起来。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models import App, AppAsset, AppAssetInstallation, AppRelease, AppShare
from app.models.app_asset_model import AssetStatus, InstallStatus, LinkMode

logger = logging.getLogger(__name__)

ReleaseSource = Literal["own", "reference", "copy", "legacy_share"]


@dataclass(frozen=True)
class ResolvedRelease:
    """某空间被允许读取的版本，以及为什么是这个版本。"""

    app_id: uuid.UUID
    release_id: Optional[uuid.UUID]
    source: ReleaseSource
    asset_id: Optional[uuid.UUID] = None
    installation_id: Optional[uuid.UUID] = None


def app_cache_key(app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]) -> str:
    """带 workspace 维度的应用缓存键（§5.3）。

    没有空间上下文（API Key 调用）时落到 `none` 段，两种形态永不碰撞。
    """

    segment = str(workspace_id) if workspace_id else "none"
    return f"cache:v2:app:{app_id}:ws:{segment}"


def _installation_stmt(app_id: uuid.UUID, workspace_id: uuid.UUID):
    """指向 `app_id` 的、该空间 active 的安装记录。"""

    return (
        select(AppAssetInstallation, AppAsset)
        .join(AppAsset, AppAsset.id == AppAssetInstallation.asset_id)
        .where(
            AppAsset.source_app_id == app_id,
            AppAssetInstallation.target_workspace_id == workspace_id,
            AppAssetInstallation.status == InstallStatus.ACTIVE,
        )
        .order_by(AppAssetInstallation.created_at.desc())
    )


def _legacy_share_stmt(app_id: uuid.UUID, workspace_id: uuid.UUID):
    """兼容期（§11.1 M3）：尚未回填到安装表的历史共享。"""

    return select(AppShare.id).where(
        AppShare.source_app_id == app_id,
        AppShare.target_workspace_id == workspace_id,
        AppShare.is_active.is_(True),
    )


def _pick_release_id(
    *,
    app: App,
    installation: Optional[AppAssetInstallation],
    asset: Optional[AppAsset],
) -> tuple[Optional[uuid.UUID], ReleaseSource]:
    """把一条安装记录翻译成消费方应当读取的 release id。"""

    if installation is None:
        return app.current_release_id, "legacy_share"

    if installation.link_mode == LinkMode.COPY:
        # 副本是本地应用；正常情况下第一个分支已覆盖，这里只做兜底。
        return installation.installed_release_id or app.current_release_id, "copy"

    if asset is None or asset.status != AssetStatus.PUBLISHED:
        # 取消共享即刻让只读引用失效（§6.1）
        raise BusinessException("该应用已取消共享", BizCode.WORKSPACE_NO_ACCESS)
    return asset.published_release_id, "reference"


class AssetReleaseResolver:
    """管理面（同步会话）版本解析。"""

    def __init__(self, db: Session):
        self.db = db

    def resolve(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> ResolvedRelease:
        app = self.db.scalar(select(App).where(App.id == app_id))
        if app is None:
            raise BusinessException("应用不存在", BizCode.APP_NOT_FOUND)

        if workspace_id is None or app.workspace_id == workspace_id:
            return ResolvedRelease(
                app_id=app.id, release_id=app.current_release_id, source="own"
            )

        row = self.db.execute(_installation_stmt(app.id, workspace_id)).first()
        if row is None:
            legacy = self.db.scalar(_legacy_share_stmt(app.id, workspace_id).limit(1))
            if legacy is None:
                raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)
            logger.warning(
                "共享记录尚未回填到资源中心安装表，按旧路径解析",
                extra={"app_id": str(app.id), "workspace_id": str(workspace_id)},
            )
            return ResolvedRelease(
                app_id=app.id, release_id=app.current_release_id, source="legacy_share"
            )

        installation, asset = row
        release_id, source = _pick_release_id(
            app=app, installation=installation, asset=asset
        )
        return ResolvedRelease(
            app_id=app.id,
            release_id=release_id,
            source=source,
            asset_id=asset.id if asset else None,
            installation_id=installation.id,
        )

    def resolve_release(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> Optional[AppRelease]:
        """直接解析到 ORM 行；未发布返回 None。"""

        resolved = self.resolve(app_id=app_id, workspace_id=workspace_id)
        if resolved.release_id is None:
            return None
        release = self.db.get(AppRelease, resolved.release_id)
        if release is None or not release.is_active:
            raise BusinessException("发布版本不存在或已下线", BizCode.RELEASE_NOT_FOUND)
        return release

    def can_access(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> bool:
        """给不允许抛错的守卫用的布尔形态。"""

        try:
            self.resolve(app_id=app_id, workspace_id=workspace_id)
        except BusinessException:
            return False
        return True


class AsyncAssetReleaseResolver:
    """运行面（异步会话，`app_api_controller` / chat）版本解析。

    SQL 与规则和同步版完全一致，只是需要 await。不合并成一个会话无关的函数，
    是因为 `Session` 与 `AsyncSession` 无法在不引入适配层的情况下统一——而适配层
    会把「在 async 处理里误调同步方法」这件事藏起来。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> ResolvedRelease:
        app = await self.db.scalar(select(App).where(App.id == app_id))
        if app is None:
            raise BusinessException("应用不存在", BizCode.APP_NOT_FOUND)

        if workspace_id is None or app.workspace_id == workspace_id:
            return ResolvedRelease(
                app_id=app.id, release_id=app.current_release_id, source="own"
            )

        row = (await self.db.execute(_installation_stmt(app.id, workspace_id))).first()
        if row is None:
            legacy = await self.db.scalar(
                _legacy_share_stmt(app.id, workspace_id).limit(1)
            )
            if legacy is None:
                raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)
            return ResolvedRelease(
                app_id=app.id, release_id=app.current_release_id, source="legacy_share"
            )

        installation, asset = row
        release_id, source = _pick_release_id(
            app=app, installation=installation, asset=asset
        )
        return ResolvedRelease(
            app_id=app.id,
            release_id=release_id,
            source=source,
            asset_id=asset.id if asset else None,
            installation_id=installation.id,
        )

    async def resolve_release(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> Optional[AppRelease]:
        resolved = await self.resolve(app_id=app_id, workspace_id=workspace_id)
        if resolved.release_id is None:
            return None
        release = await self.db.get(AppRelease, resolved.release_id)
        if release is None or not release.is_active:
            raise BusinessException("发布版本不存在或已下线", BizCode.RELEASE_NOT_FOUND)
        return release

    async def can_access(
        self, *, app_id: uuid.UUID, workspace_id: Optional[uuid.UUID]
    ) -> bool:
        try:
            await self.resolve(app_id=app_id, workspace_id=workspace_id)
        except BusinessException:
            return False
        return True


__all__ = [
    "AssetReleaseResolver",
    "AsyncAssetReleaseResolver",
    "ResolvedRelease",
    "app_cache_key",
]