"""Resource-center tables: tenant-level catalog entries and pull records.

Two tables only; the cross-service submission fallback (`market_sync`) lands with
the market-link work. DDL is owned by the monolith migration chain (see
`models/base.py`) — this module only declares the mapping.

Design notes that differ from `app_shares`:
- `app_assets` is tenant-scoped and pins a release, so a source release does not
  leak to consumers; `app_shares` is workspace-to-workspace with no version.
- `app_asset_installations` dedupes by source via **two partial unique indexes**,
  because a NULL `asset_id` makes a single composite unique index useless in
  Postgres (every NULL compares distinct).
"""
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class AssetStatus:
    """Entry visibility. Only sharing / un-sharing drives it — no operator toggle."""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class ShareMode:
    """Switch 2 of the publish dialog. Exactly two values."""

    READONLY = "readonly"
    EDITABLE = "editable"


class InstallSource:
    CATALOG = "catalog"
    MARKET = "market"


class LinkMode:
    REFERENCE = "reference"
    COPY = "copy"


class InstallStatus:
    ACTIVE = "active"
    REMOVED = "removed"


class AppAsset(Base):
    """A tenant-level catalog entry: one row per app per tenant."""

    __tablename__ = "app_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
        comment="条目归属租户，所有查询的第一道过滤",
    )
    source_app_id = Column(
        UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="源应用",
    )
    source_workspace_id = Column(
        UUID(as_uuid=True), nullable=False, comment="上架时快照，展示「来自哪个空间」",
    )
    published_release_id = Column(
        UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=False, index=True,
        comment="钉住的版本，消费方跟这个走",
    )
    published_version = Column(Integer, nullable=False, comment="冗余，列表排序/展示用，免 join")
    share_mode = Column(
        String(16), nullable=False, default=ShareMode.READONLY,
        comment="readonly（只读引用）| editable（可复制为独立副本）",
    )
    market_resource_id = Column(
        UUID(as_uuid=True), nullable=True, comment="已提交社区市场时的 market_resources.id（跨服务裸 UUID，不建 FK）",
    )
    market_synced_release_id = Column(
        UUID(as_uuid=True), nullable=True,
        comment="最近一次提交到市场的版本，与 published_release_id 分开记录",
    )
    status = Column(
        String(16), nullable=False, default=AssetStatus.PUBLISHED, server_default="published",
        comment="draft | published | unpublished",
    )
    view_count = Column(
        Integer, nullable=False, default=0, server_default="0",
        comment="浏览次数，每次打开详情 +1（与 install_count 是两个指标）",
    )
    description = Column(
        Text, nullable=True,
        comment="= 钉住版本的 app_releases.description，详情页正文与列表卡片同源",
    )
    display_payload = Column(
        JSON, default=dict, comment="真实配置，同租户内无需脱敏",
    )
    created_by = Column(UUID(as_uuid=True), nullable=True, comment="users.id")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)

    published_release = relationship("AppRelease", foreign_keys=[published_release_id], viewonly=True)

    __table_args__ = (
        # One entry per app per tenant: re-sharing updates it (version forward),
        # it never creates a second row.
        UniqueConstraint("tenant_id", "source_app_id", name="uq_app_asset_tenant_app"),
    )


class AppAssetInstallation(Base):
    """One pull / install record. Both chains share this table."""

    __tablename__ = "app_asset_installations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(
        UUID(as_uuid=True), ForeignKey("app_assets.id"), nullable=True, index=True,
        comment="资源中心拉取时必填；市场来源的安装无对应条目，为 NULL",
    )
    source = Column(
        String(16), nullable=False, default=InstallSource.CATALOG,
        comment="catalog（资源中心拉取）| market（社区市场安装）",
    )
    market_resource_id = Column(
        UUID(as_uuid=True), nullable=True, index=True,
        comment="source=market 时必填，市场侧资源 id",
    )
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    target_workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True,
        comment="拉到哪个空间",
    )
    permission = Column(
        String(16), nullable=False, default=ShareMode.READONLY,
        comment="readonly | editable；source=market 时恒为 editable",
    )
    link_mode = Column(
        String(16), nullable=False, default=LinkMode.REFERENCE,
        comment="reference（只读）| copy（编辑副本）；source=market 时恒为 copy",
    )
    installed_release_id = Column(
        UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=True,
        comment="拉取时刻的版本；source=market 时是落地后本地新建应用的 release",
    )
    local_app_id = Column(
        UUID(as_uuid=True), ForeignKey("apps.id"), nullable=True,
        comment="copy 时为副本 app；reference 时为 NULL",
    )
    follow_version = Column(
        Boolean, nullable=False, default=True,
        comment="reference 恒 true；copy 一旦本地编辑置 false",
    )
    diverged_at = Column(DateTime, nullable=True, comment="副本首次本地编辑时间")
    status = Column(
        String(16), nullable=False, default=InstallStatus.ACTIVE, server_default="active",
        comment="active | removed",
    )
    result = Column(
        String(16), nullable=False, default="success", comment="success | partial",
    )
    missing_report = Column(JSON, nullable=True, comment="result=partial 时的 MissingDependency[]")
    created_by = Column(UUID(as_uuid=True), nullable=True, comment="users.id")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    asset = relationship("AppAsset", foreign_keys=[asset_id], viewonly=True)

    __table_args__ = (
        # Split by source: a composite unique index including a nullable asset_id
        # does not dedupe — Postgres treats every NULL as distinct.
        Index(
            "uq_asset_install",
            "asset_id",
            "target_workspace_id",
            unique=True,
            postgresql_where=text("status = 'active' AND asset_id IS NOT NULL"),
        ),
        Index(
            "uq_market_install",
            "market_resource_id",
            "target_workspace_id",
            unique=True,
            postgresql_where=text("status = 'active' AND market_resource_id IS NOT NULL"),
        ),
    )


class MarketSyncStatus:
    """我们主库独有的投稿中间态，与市场审核状态机不是一套。"""

    PENDING_SYNC = "pending_sync"
    SYNCED = "synced"
    SYNC_FAILED = "sync_failed"


class MarketSync(Base):
    """跨服务投稿兜底：本地已提交、市场未成功时留一行，可重试。"""

    __tablename__ = "market_sync"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    asset_id = Column(
        UUID(as_uuid=True), ForeignKey("app_assets.id"), nullable=False, index=True,
        comment="对应的资源中心条目",
    )
    release_id = Column(
        UUID(as_uuid=True), ForeignKey("app_releases.id"), nullable=False,
        comment="本次投稿的版本",
    )
    bundle_content_hash = Column(
        String(64), nullable=True,
        comment="当初投出去那份包的哈希；重试时据此复用同一份包，不重导",
    )
    status = Column(
        String(16), nullable=False, default=MarketSyncStatus.PENDING_SYNC,
        index=True, comment="pending_sync | synced | sync_failed",
    )
    market_resource_id = Column(UUID(as_uuid=True), nullable=True, comment="成功回写")
    market_submission_id = Column(
        String(64), nullable=True,
        comment="应用投稿拿到的 submission_id；整包审核编排按它推进",
    )
    dependency_submissions = Column(
        JSON, nullable=True,
        comment="随包依赖的投稿记录 [{local_ref, resource_type, version, submission_id, resource_id, content_hash}]",
    )
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)