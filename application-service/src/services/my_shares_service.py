"""「我的共享」分享方视角列表：一行 = 一个「应用 × 共享范围」。

契约（Apifox v0.5.5 / `GET /apps/my-shared-out`，接口 ID 427331831）与设计文档
`docs/应用共享与市场/应用共享与社区市场改造方案.md` §4.4 是权威，本模块只负责取数：

- **资源中心行** ← `app_assets.status = published`（`share_mode` 决定 `editable`）
- **社区市场行** ← 存在 `market_sync` 行即说明投过稿（`market_sync` 是投稿兜底表，
  本地提交成功、市场未收到时也留行），状态分两层：
  1. 本地 `market_sync.status = pending_sync | sync_failed` 优先（市场还没收到，问它没意义）
  2. 其余问市场一次（`MarketGateway.publication_status`，一次批量，最多 100 个）

为什么不能只按 `app_shares` 出：`app_shares` 是「共享给哪个空间」的老模型，资源中心
与社区市场都不是空间维度（`app_assets` 一个应用在一个租户内只有一条，见
`models/app_asset_model.py::uq_app_asset_tenant_app`），按目标空间铺行会同一件事出现多行。

市场不可用时整批降 `unknown` 并照常返回列表——列表页的可用性优先于状态准确性，
与 `services/external_status_service.py` 同一条约定。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.app_asset_model import (
    AppAsset,
    AssetStatus,
    MarketSync,
    MarketSyncStatus,
    ShareMode,
)
from ..models.app_model import App
from ..models.app_release_model import AppRelease
from ..utils.datetime_utils import to_timestamp_ms

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行时不需要
    from .app_service import AppService

logger = logging.getLogger(__name__)

SCOPE_RESOURCE_CENTER = "resource_center"
SCOPE_COMMUNITY_MARKET = "community_market"

# 市场侧已审 / 待审的状态，直接透出（与 `接口文档.md` §3.3 `latest_publication.status` 同枚举）
_MARKET_STATUSES = frozenset({"pending", "approved", "rejected", "withdrawn"})
UNKNOWN = "unknown"

# 市场批量查询上限（他的 `/internal/v1/catalog/publications/status` 一次最多 100 个）
STATUS_BATCH_LIMIT = 100


def _record_get(record: Any, key: str) -> Any:
    """网关可能返回 dataclass（协议）或 dict（企业 HTTP 实现），两种都认。"""

    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


class MySharesService:
    """分享方视角：本工作空间共享到资源中心 / 社区市场的记录。"""

    def __init__(self, db: Session, app_service: "AppService | None" = None):
        self.db = db
        if app_service is None:
            # 延迟 import：`app_service` 会连带整棵模型树/model_clients，
            # 本模块的行铺法可以脱离数据库单测，没必要在 import 期拉起来。
            from .app_service import AppService as _AppService

            app_service = _AppService(db)
        self.app_service = app_service

    # ==================== 列表 ====================

    def list_rows(
            self,
            *,
            tenant_id: uuid.UUID,
            workspace_id: uuid.UUID,
            market: Any | None = None,
            page: int = 1,
            pagesize: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """返回 `(rows, total)`；`total` 是**行数**（应用 × 范围），不是应用个数。"""

        assets = self._load_assets(tenant_id=tenant_id, workspace_id=workspace_id)
        if not assets:
            return [], 0

        apps = self._load_apps({asset.source_app_id for asset in assets})
        # 同一个条目在 market_sync 里可能有多行（每次投稿一行），取最近一次
        syncs = self._latest_sync_per_asset(self._load_syncs(assets))
        releases = self._load_releases(
            {asset.published_release_id for asset in assets}
            | {sync.release_id for sync in syncs.values()}
        )
        market_status = self._market_status(
            [str(asset.source_app_id) for asset in assets if asset.id in syncs],
            tenant_id=tenant_id,
            market=market,
        )

        rows: list[dict[str, Any]] = []
        for asset in assets:
            app = apps.get(asset.source_app_id)
            if app is None:
                # 应用已被删（`ondelete=CASCADE` 下条目也会没），跳过而不是抛错
                continue
            pinned = releases.get(asset.published_release_id)
            if pinned is None:
                continue

            # `market_sync` 按条目（asset_id）挂，不是按应用——同一应用换空间共享会另起条目
            sync = syncs.get(asset.id)
            legacy = self._base_row(app=app, release=pinned, asset=asset)

            if asset.status == AssetStatus.PUBLISHED and asset.is_active:
                rows.append({
                    **legacy,
                    "scope": SCOPE_RESOURCE_CENTER,
                    "share_status": "shared",
                    "editable": asset.share_mode == ShareMode.EDITABLE,
                    "shared_at": to_timestamp_ms(asset.updated_at),
                    "market_resource_id": None,
                    "pending_publication_id": None,
                })

            if sync is None:
                continue

            market_version = releases.get(sync.release_id) or pinned
            status = self._market_row_status(sync, market_status.get(str(app.id)))
            rows.append({
                **legacy,
                "version": market_version.version_name,
                "version_number": market_version.version,
                "scope": SCOPE_COMMUNITY_MARKET,
                "share_status": status,
                # 市场只有「复制为可编辑副本」一条落地路径，没有可选项（§1.2）；
                # 字段恒 true，前端把这一行的开关置灰而不是隐藏。
                "editable": True,
                "shared_at": to_timestamp_ms(sync.updated_at or sync.created_at),
                "market_resource_id": (
                    str(sync.market_resource_id) if sync.market_resource_id else None
                ),
                # 「撤回申请」直接用这个 id 调 POST /publications/{id}/withdraw。
                # 只在 pending 时有值——前端据此决定按钮出现，不要按状态再推一遍。
                "pending_publication_id": (
                    sync.market_submission_id
                    if status == "pending" and sync.market_submission_id else None
                ),
            })

        # `shared_at` 倒序；同一时刻同一应用的两行让资源中心在前，保证翻页稳定。
        # 两个键方向不同：`shared_at` 递减，`scope` 排名递增，所以对时间取负后
        # 一起升序排，不能用 `reverse=True`（那会连排名一起反转，市场跑到前面）。
        rows.sort(
            key=lambda row: (
                -(row["shared_at"] or 0),
                0 if row["scope"] == SCOPE_RESOURCE_CENTER else 1,
            ),
        )
        total = len(rows)
        start = max(page - 1, 0) * pagesize
        return rows[start:start + pagesize], total

    def fill_ontology(self, rows: list[dict[str, Any]]) -> None:
        """就地回填 `ontology`（本体二期字段，与 `GET /apps/{id}` 同源）。

        必须在行序列化**之后**调用：远端 binder 会先结束事务，之后 ORM 对象就过期了
        （同 `app_share_service._share_schemas` 的注释）。社区版为 `None`。
        """

        if not rows:
            return
        app_ids = list({uuid.UUID(row["app_id"]) for row in rows})
        ontology_map = self.app_service._load_ontology_map(app_ids)
        for row in rows:
            ontology = (ontology_map or {}).get(uuid.UUID(row["app_id"]))
            row["ontology"] = ontology if ontology else None

    # ==================== 取数 ====================

    def _load_assets(
            self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[AppAsset]:
        """本工作空间共享出去的条目。

        按 `source_workspace_id` 过滤而不是租户内全量：条目虽然租户级唯一，但「我的
        共享」是**空间**视角的页面，别人在另一个空间共享的应用不该出现在这里。
        只开开关 3 的载体条目（`status=unpublished`）也要查出来——它承载市场行。
        """

        return list(
            self.db.scalars(
                select(AppAsset)
                .where(
                    AppAsset.tenant_id == tenant_id,
                    AppAsset.source_workspace_id == workspace_id,
                    AppAsset.is_active.is_(True),
                )
                .order_by(AppAsset.updated_at.desc())
            ).all()
        )

    def _load_syncs(self, assets: list[AppAsset]) -> list[MarketSync]:
        if not assets:
            return []
        return list(
            self.db.scalars(
                select(MarketSync)
                .where(MarketSync.asset_id.in_([asset.id for asset in assets]))
                .order_by(MarketSync.updated_at.desc())
            ).all()
        )

    @staticmethod
    def _latest_sync_per_asset(syncs: list[MarketSync]) -> dict[uuid.UUID, MarketSync]:
        """一个条目可能投过多次（每次新版本一行），列表只展示最近一次。"""

        latest: dict[uuid.UUID, MarketSync] = {}
        for sync in syncs:
            current = latest.get(sync.asset_id)
            if current is None or (sync.updated_at or sync.created_at) > (
                    current.updated_at or current.created_at
            ):
                latest[sync.asset_id] = sync
        return latest

    def _load_apps(self, app_ids: set[uuid.UUID]) -> dict[uuid.UUID, App]:
        """批量取应用行，**只取未软删的**。

        `delete_app` 只软删 `apps.is_active`，不碰 `app_assets`：条目仍是
        `published`，`_base_row` 的字段也全都来自 `app_releases` 快照。所以这里
        不过滤就会把已删应用照常铺成「我的共享」的一行，而资源中心列表、应用
        列表、详情/安装鉴权都各自用 `App.is_active` 或 `_get_app_or_404` 挡住了
        它——同一份数据在不同页面上给出不同答案。

        过滤放在取数处而不是 `list_rows` 的行循环里：`market_sync` 行与资源中心行
        复用同一个 `app`，漏到循环里会连市场行一起漏出去。
        """

        if not app_ids:
            return {}
        return {
            app.id: app
            for app in self.db.scalars(
                select(App).where(
                    App.id.in_(app_ids),
                    App.is_active.is_(True),
                )
            ).all()
        }

    def _load_releases(self, release_ids: set[uuid.UUID]) -> dict[uuid.UUID, AppRelease]:
        if not release_ids:
            return {}
        return {
            release.id: release
            for release in self.db.scalars(
                select(AppRelease).where(AppRelease.id.in_(release_ids))
            ).all()
        }

    # ==================== 市场状态 ====================

    def _market_status(
            self,
            local_resource_ids: list[str],
            *,
            tenant_id: uuid.UUID,
            market: Any | None,
    ) -> dict[str, str]:
        """批量问市场「这个应用最新投稿是什么状态」；失败整批 `unknown`。

        只查**已投稿**的应用（`market_sync` 有行的），没投过的不会出现在这里。
        """

        if not local_resource_ids:
            return {}
        if market is None:
            return {resource_id: UNKNOWN for resource_id in local_resource_ids}

        resolved: dict[str, str] = {}
        unique_ids = list(dict.fromkeys(local_resource_ids))
        for start in range(0, len(unique_ids), STATUS_BATCH_LIMIT):
            chunk = unique_ids[start:start + STATUS_BATCH_LIMIT]
            try:
                records = asyncio.run(
                    market.publication_status("app", chunk, str(tenant_id))
                )
            except Exception as error:  # noqa: BLE001 - 列表可用性优先于状态准确性
                logger.warning(
                    "我的共享：市场状态批量查询失败，整批降级 unknown",
                    extra={"count": len(chunk), "error": str(error)[:200]},
                )
                resolved.update({resource_id: UNKNOWN for resource_id in chunk})
                continue
            resolved.update(self._fold_statuses(records, chunk))
        return resolved

    @staticmethod
    def _fold_statuses(records: Any, requested: list[str]) -> dict[str, str]:
        folded: dict[str, str] = {}
        items = records.get("items") if isinstance(records, dict) else records
        for record in items or []:
            key = _record_get(record, "local_resource_id") or _record_get(record, "resource_id")
            # 列表要的是「最新一次投稿」的状态，不是资源整体上下架状态
            status = _record_get(record, "latest_publication_status") or _record_get(
                record, "status"
            )
            if key:
                folded[str(key)] = str(status or UNKNOWN)
        return {resource_id: folded.get(resource_id, UNKNOWN) for resource_id in requested}

    @staticmethod
    def _market_row_status(sync: MarketSync, market_status: str | None) -> str:
        """本地兜底态优先：市场还没收到（`pending_sync`）时问它只会得到 unknown。"""

        if sync.status in (MarketSyncStatus.PENDING_SYNC, MarketSyncStatus.SYNC_FAILED):
            return sync.status
        if market_status in _MARKET_STATUSES:
            return market_status
        return UNKNOWN

    # ==================== 行公共字段 ====================

    @staticmethod
    def _base_row(
            *, app: App, release: AppRelease, asset: AppAsset
    ) -> dict[str, Any]:
        payload = asset.display_payload or {}
        return {
            "app_id": str(app.id),
            "app_name": release.name,
            "app_type": release.type,
            "icon": payload.get("icon") or None,
            "icon_type": payload.get("icon_type") or None,
            "scope": SCOPE_RESOURCE_CENTER,
            "share_status": "shared",
            "version": release.version_name,
            "version_number": release.version,
            "editable": False,
            "shared_at": to_timestamp_ms(asset.updated_at),
            "asset_id": str(asset.id),
            "market_resource_id": None,
            "pending_publication_id": None,
            "ontology": None,
        }


__all__ = [
    "MySharesService",
    "SCOPE_COMMUNITY_MARKET",
    "SCOPE_RESOURCE_CENTER",
    "UNKNOWN",
]