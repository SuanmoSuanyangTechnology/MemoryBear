"""「我的共享」分享方视角列表：一行 = 一个「应用 × 共享范围」。

契约（Apifox v0.5.5 / `GET /apps/my-shared-out`，接口 ID 427331831）与设计文档
`docs/应用共享与市场/应用共享与社区市场改造方案.md` §4.4 是权威，本模块只负责取数：

- **资源中心行** ← `app_assets.status = published`（`share_mode` 决定 `editable`）
- **社区市场行** ← 存在 `market_sync` 行即说明投过稿（`market_sync` 是投稿兜底表，
  本地提交成功、市场未收到时也留行），状态分两层：
  1. 本地 `market_sync.status = pending_sync | sync_failed` 优先（市场还没收到，问它没意义）
  2. 其余问市场一次（`MarketGateway.publication_status`，一次批量，最多 100 个）

两层之外还有**上架状态**：`share_status` 只表达投稿申请的审核结果，同一个
`approved` 资源可能是「在架」「发布方下架待自助上架」「平台下架」三种。所以市场行额外
透出 `listing_status` / `delist_actor` / `can_delist` / `can_relist`（契约 v0.14 §3.3 同口径），
让前端按 `can_*` 画按钮，不再从 `share_status` 推一遍。资源中心没有上下架概念——
下架就是取消共享，这四个字段恒 `null` / `null` / `false` / `false`。

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

# 拿不到市场数据时（本地待同步 / 市场不可用）的四个上架字段：
# 不报错误、不伪装成「已下架」——前端据此隐藏「下架 / 自助上架」按钮。
_EMPTY_MARKET_STATE: dict[str, Any] = {
    "listing_status": None,
    "delist_actor": None,
    "can_delist": False,
    "can_relist": False,
}
_UNKNOWN_MARKET_ROW: dict[str, Any] = {"status": UNKNOWN, **_EMPTY_MARKET_STATE}

# 市场批量查询上限（他的 `/internal/v1/catalog/publications/status` 一次最多 100 个）
STATUS_BATCH_LIMIT = 100


def _record_get(record: Any, key: str) -> Any:
    """网关可能返回 dataclass（协议）或 dict（企业 HTTP 实现），两种都认。"""

    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _can_delist(record: Any, listing_status: str | None) -> bool:
    """§3.3：市场给了 `can_delist` 就用它，没给时按「在架即可下架」推导。

    发布者身份由市场在校验阶段负责，本地不重复判定——与
    `external_catalog_service._can_delist` 同一口径，两个入口不能给出不同按钮。
    """

    value = _record_get(record, "can_delist")
    if value is not None:
        return bool(value)
    return listing_status == "listed"


def _can_relist(
        record: Any, listing_status: str | None, delist_actor: str | None
) -> bool:
    """§3.3：只有发布方自己下架的 `pending_listing` 能自助上架。

    运营仅保存的 `pending_listing`（`delist_actor=null`）与平台 `delisted` 都不可。
    """

    value = _record_get(record, "can_relist")
    if value is not None:
        return bool(value)
    return listing_status == "pending_listing" and delist_actor == "publisher"


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
        market_state = self._market_status(
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
            state = market_state.get(str(app.id)) or dict(_UNKNOWN_MARKET_ROW)
            status = self._market_row_status(sync, state.get("status"))
            rows.append({
                **legacy,
                "version": market_version.version_name,
                "version_number": market_version.version,
                # 与市场 versions 接口的 item.release_id 同源（都是 `AppRelease.id`），
                # 前端据此把「我的共享」这一行对到该资源的版本记录上。不能按
                # `version` 字符串匹配：市场只列已通过版本，本行取最近一次投稿，
                # 审核中的那一版在版本记录里根本不存在；而 `version_name` 本身
                # 在 `app_releases` 上也没有唯一约束。
                "release_id": str(sync.release_id),
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
                # 上架状态与两个自助动作的可用性，直接取市场判定（§3.3 同口径）。
                # `share_status=approved` 不代表在架：可能已被发布方下架或平台下架。
                "listing_status": state.get("listing_status"),
                "delist_actor": state.get("delist_actor"),
                "can_delist": state.get("can_delist", False),
                "can_relist": state.get("can_relist", False),
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
                # 投稿先后只看 `created_at`：`updated_at` 带 `onupdate`，同步重试 /
                # 状态推进都会刷新它，用它排序会让「较早的投稿晚完成」压过更新的那版。
                .order_by(MarketSync.created_at.desc(), MarketSync.id.desc())
            ).all()
        )

    @staticmethod
    def _latest_sync_per_asset(syncs: list[MarketSync]) -> dict[uuid.UUID, MarketSync]:
        """一个条目可能投过多次（每次新版本一行），列表只展示最近一次。

        「最近」= 最后一次**发起**投稿，所以排序键是 `created_at`（不可变），
        不是 `updated_at`。`updated_at` 会在派发、重试、状态回写时被刷新：
        第 1 版投稿先发、第 2 版后发，但第 1 版的同步行最后才落定，按
        `updated_at` 取就会把第 1 版当成最新——列表于是显示旧的 `release_id` /
        `version`，与市场版本记录对不上。

        平局按 `id` 决胜，保证同一批数据无论传入顺序如何都取到同一行。
        """

        latest: dict[uuid.UUID, MarketSync] = {}
        for sync in syncs:
            current = latest.get(sync.asset_id)
            if current is None or (sync.created_at, sync.id) > (
                    current.created_at, current.id
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
    ) -> dict[str, dict[str, Any]]:
        """批量问市场「这个应用现在什么状态」；失败整批 `unknown`。

        每个应用一份状态字典（`status` + 四个上架字段），行里直接铺开。
        只查**已投稿**的应用（`market_sync` 有行的），没投过的不会出现在这里。
        """

        if not local_resource_ids:
            return {}
        if market is None:
            return {resource_id: dict(_UNKNOWN_MARKET_ROW) for resource_id in local_resource_ids}

        resolved: dict[str, dict[str, Any]] = {}
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
                resolved.update(
                    {resource_id: dict(_UNKNOWN_MARKET_ROW) for resource_id in chunk}
                )
                continue
            resolved.update(self._fold_statuses(records, chunk))
        return resolved

    @staticmethod
    def _fold_statuses(records: Any, requested: list[str]) -> dict[str, dict[str, Any]]:
        """把市场返回的一条记录折成行可用的状态字典。

        `status` 取最新一次投稿的审核状态（列表要的是这个，不是资源整体状态）；
        缺记录或状态不合法时整行降 `unknown`，四个上架字段一并置空——
        不能拿半份数据画按钮，否则会出现「状态未知但能点下架」。

        **字段名的读取顺序是硬约束**：市场 §6.4 批量状态接口返回的是
        `review_status`（`market_publication_service._resource_status_item`），
        `HttpMarketGateway.publication_status` 又原样透传 JSON，所以这里必须先认它。
        只读 `latest_publication_status` / `status` 会让每条投稿都静默降成
        `unknown`——而同一条记录的 `listing_status` 正常，症状是「上架状态对、
        审核状态却未知」。后两个名字留作早期实现的兜底，顺序与
        `external_catalog_service.publication_status` 保持一致，两个入口不能给出
        不同的审核状态。
        """

        folded: dict[str, dict[str, Any]] = {}
        items = records.get("items") if isinstance(records, dict) else records
        for record in items or []:
            key = _record_get(record, "local_resource_id") or _record_get(record, "resource_id")
            if not key:
                continue
            # 列表要的是「最新一次投稿」的状态，不是资源整体上下架状态
            status = (
                _record_get(record, "review_status")
                or _record_get(record, "latest_publication_status")
                or _record_get(record, "status")
            )
            if status not in _MARKET_STATUSES:
                status = UNKNOWN
            listing_status = _record_get(record, "listing_status")
            delist_actor = _record_get(record, "delist_actor")
            folded[str(key)] = {
                "status": status,
                "listing_status": listing_status,
                "delist_actor": delist_actor,
                "can_delist": _can_delist(record, listing_status),
                "can_relist": _can_relist(record, listing_status, delist_actor),
            }
        return {
            resource_id: folded.get(resource_id) or dict(_UNKNOWN_MARKET_ROW)
            for resource_id in requested
        }

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
            # 资源中心行给钉住版本的 id，与市场行同名字段对齐：前端不必为一个
            # 字段写两套取值路径，只是资源中心没有对应的版本记录接口。
            "release_id": str(release.id),
            "editable": False,
            "shared_at": to_timestamp_ms(asset.updated_at),
            "asset_id": str(asset.id),
            "market_resource_id": None,
            "pending_publication_id": None,
            # 资源中心没有上下架：下架就是取消共享（`DELETE /apps/assets/{asset_id}`），
            # 市场侧的四个字段在这里恒空。市场行会覆盖它们。
            "listing_status": None,
            "delist_actor": None,
            "can_delist": False,
            "can_relist": False,
            "ontology": None,
        }


__all__ = [
    "MySharesService",
    "SCOPE_COMMUNITY_MARKET",
    "SCOPE_RESOURCE_CENTER",
    "UNKNOWN",
]