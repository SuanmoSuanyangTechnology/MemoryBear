"""package_plans / package_plan_versions 只读映射（premium 平台域表，套餐配额解析用）。

套餐额度口径（与宿主 `TenantSubscriptionService.get_effective_quota` 一致）：订阅时锁定的
`package_plan_versions.version_snapshot` 优先；快照缺失回落到 `package_plans.quotas`。
`tier_level` 用于多订阅时取最高档。
"""

from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..base import ReadOnlyBase


class PackagePlan(ReadOnlyBase):
    __tablename__ = "package_plans"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tier_level = Column(Integer, nullable=False)
    quotas = Column(JSONB, nullable=True)


class PackagePlanVersion(ReadOnlyBase):
    __tablename__ = "package_plan_versions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    package_plan_id = Column(UUID(as_uuid=True), nullable=False)
    version = Column(String(50), nullable=False)
    version_snapshot = Column(JSONB, nullable=False)
