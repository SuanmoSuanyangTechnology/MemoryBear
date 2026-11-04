"""资源中心表：租户级条目与拉取记录。

只有两张表；跨服务投稿兜底表 `market_sync` 随后续市场链路一起落地。
DDL 由单体迁移链统一下发（`core/api/migrations`），这里只声明映射。

与 `app_shares` 的关键差异：
- `app_assets` 是租户级并钉住一个 release，所以源方发版不会穿透到消费方；
  `app_shares` 是空间到空间、没有版本概念。
- `app_asset_installations` 按来源用**两条部分唯一索引**去重：`asset_id` 可为 NULL 的
  联合唯一索引在 Postgres 里形同虚设（每个 NULL 互不相等）。
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

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class AssetStatus:
    """条目可见性。只由共享/取消共享驱动，没有运营上下架开关。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class ShareMode:
    """发布弹窗开关 2。只有两个值。"""

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
    """租户级资源中心条目：一个应用在一个租户内只有一行。"""

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
        UUID(as_uuid=True), nullable=True,
        comment="已提交社区市场时的 market_resources.id（跨服务裸 UUID，不建 FK）",
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
    display_payload = Column(JSON, default=dict, comment="真实配置，同租户内无需脱敏")
    created_by = Column(UUID(as_uuid=True), nullable=True, comment="users.id")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)

    published_release = relationship(
        "AppRelease", foreign_keys=[published_release_id], viewonly=True
    )

    __table_args__ = (
        # 一个应用在一个租户内只有一个条目：重复共享是版本前移，不是新建
        UniqueConstraint("tenant_id", "source_app_id", name="uq_app_asset_tenant_app"),
    )


class AppAssetInstallation(Base):
    """一次拉取/安装记录。资源中心与社区市场两条链路共用这张表。"""

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
    result = Column(String(16), nullable=False, default="success", comment="success | partial")
    missing_report = Column(JSON, nullable=True, comment="result=partial 时的 MissingDependency[]")
    created_by = Column(UUID(as_uuid=True), nullable=True, comment="users.id")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    asset = relationship("AppAsset", foreign_keys=[asset_id], viewonly=True)

    __table_args__ = (
        # 按来源拆开：含可空 asset_id 的联合唯一索引不会去重，Postgres 视每个 NULL 互不相等
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
    """跨服务投稿兜底：本地已提交、市场未成功时留一行，可重试。

    它只存在于我们主库，记录「本地已提交、市场未成功」的中间态，
    不是市场审核状态机的一部分。
    """

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