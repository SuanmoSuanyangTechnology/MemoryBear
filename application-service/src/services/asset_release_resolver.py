"""Single point of truth for "which release should this workspace read?".

After the catalog landed, a shared app has **two** possible release pointers for
the same `app_id`: the source workspace's `current_release_id`, and the release
pinned by the tenant catalog entry. The consumer must follow the pinned one, and
the pin can differ per consuming workspace. Any read path that keeps dereferencing
`app.current_release_id` directly will show the wrong snapshot — and worse, will
show a *newer* one than the entry was reviewed at.

So every shared-app read path goes through here:

| situation | resolved release |
| --- | --- |
| caller's own workspace | `app.current_release_id` (unchanged behaviour) |
| active `reference` installation | `app_assets.published_release_id` (the pin) |
| active `copy` installation | the copy's own `current_release_id` — it is a local app, so the first branch already covers it |
| no installation, legacy `app_shares` row | `app.current_release_id` (compatibility window only, §11.1 M3) |
| nothing at all | raise `3003 WORKSPACE_NO_ACCESS` |

Two extra rules that are easy to miss:

- A `reference` installation stops resolving the moment the entry becomes
  `unpublished` (cancel sharing) — copies keep working, references do not.
- The result is cached under a **workspace-scoped** key. The old
  `cache:v2:app:{app_id}` assumed one app has one release; with per-workspace
  pins that key would cross two tenants' pinned versions.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models import App, AppRelease, AppShare
from ..models.app_asset_model import (
    AppAsset,
    AppAssetInstallation,
    AssetStatus,
    InstallStatus,
    LinkMode,
)

logger = logging.getLogger(__name__)

ReleaseSource = Literal["own", "reference", "copy", "legacy_share"]


@dataclass(frozen=True)
class ResolvedRelease:
    """What a workspace is allowed to read, and why."""

    app_id: uuid.UUID
    release_id: uuid.UUID | None
    source: ReleaseSource
    asset_id: uuid.UUID | None = None
    installation_id: uuid.UUID | None = None


def app_cache_key(app_id: uuid.UUID, workspace_id: uuid.UUID | None) -> str:
    """Workspace-scoped app cache key (design §5.3).

    Falls back to a `no-ws` segment when there is no workspace context (API-key
    calls), so the two shapes never collide.
    """

    segment = str(workspace_id) if workspace_id else "none"
    return f"cache:v2:app:{app_id}:ws:{segment}"


def _installation_stmt(
    app_id: uuid.UUID, workspace_id: uuid.UUID
):
    """Active installation of any catalog entry pointing at `app_id`."""

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
    """Compatibility window (§11.1 M3): shares that have not been backfilled."""

    return select(AppShare.id).where(
        AppShare.source_app_id == app_id,
        AppShare.target_workspace_id == workspace_id,
        AppShare.is_active.is_(True),
    )


def _pick_release_id(
    *,
    app: App,
    installation: AppAssetInstallation | None,
    asset: AppAsset | None,
) -> tuple[uuid.UUID | None, ReleaseSource]:
    """Turn an installation row into the release id the consumer should read."""

    if installation is None:
        return app.current_release_id, "legacy_share"

    if installation.link_mode == LinkMode.COPY:
        # A copy is a local app; this branch is defensive (its app_id normally
        # belongs to the target workspace, so `own` wins earlier).
        return installation.installed_release_id or app.current_release_id, "copy"

    if (
        asset is None
        or asset.status != AssetStatus.PUBLISHED
        or not asset.is_active
    ):
        # 取消共享或停用条目都会立即使 reference 安装失效。
        raise BusinessException(
            "该应用已取消共享", BizCode.WORKSPACE_NO_ACCESS
        )
    return asset.published_release_id, "reference"


class AssetReleaseResolver:
    """Sync resolver for the management plane."""

    def __init__(self, db: Session):
        self.db = db

    def resolve(self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None) -> ResolvedRelease:
        app = self.db.scalar(select(App).where(App.id == app_id))
        if app is None:
            raise BusinessException("应用不存在", BizCode.APP_NOT_FOUND)

        if workspace_id is None or app.workspace_id == workspace_id:
            return ResolvedRelease(
                app_id=app.id,
                release_id=app.current_release_id,
                source="own",
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
        self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> AppRelease | None:
        """Resolve straight to the ORM row; `None` when the app is unpublished."""

        resolved = self.resolve(app_id=app_id, workspace_id=workspace_id)
        if resolved.release_id is None:
            return None
        release = self.db.get(AppRelease, resolved.release_id)
        if release is None or not release.is_active:
            raise BusinessException("发布版本不存在或已下线", BizCode.RELEASE_NOT_FOUND)
        return release

    def can_access(self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None) -> bool:
        """Boolean form for guards that must not raise."""

        try:
            self.resolve(app_id=app_id, workspace_id=workspace_id)
        except BusinessException:
            return False
        return True


class AsyncAssetReleaseResolver:
    """Async twin, for the runtime plane (`chat_use_case`, `draft_run`).

    Same SQL, same rules — only the awaiting differs. Kept as a separate class
    instead of one session-agnostic function because `Session.scalar` and
    `AsyncSession.scalar` cannot be unified without an adapter that would hide
    an accidental sync call inside an async handler.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve(
        self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None
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
        self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> AppRelease | None:
        resolved = await self.resolve(app_id=app_id, workspace_id=workspace_id)
        if resolved.release_id is None:
            return None
        release = await self.db.get(AppRelease, resolved.release_id)
        if release is None or not release.is_active:
            raise BusinessException("发布版本不存在或已下线", BizCode.RELEASE_NOT_FOUND)
        return release

    async def can_access(
        self, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None
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