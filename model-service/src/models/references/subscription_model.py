"""tenant_subscriptions 只读映射（premium 平台域表，配额解析用）。

配额只读当前生效订阅（status='active'）的锁定版本；套餐快照表（package_plans /
package_plan_versions）与资源包叠加在 M7-3 `model_quota.py` 落地时按同法补映射。
"""

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from ..base import ReadOnlyBase

ACTIVE_STATUS = "active"


class TenantSubscription(ReadOnlyBase):
    __tablename__ = "tenant_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    package_plan_id = Column(UUID(as_uuid=True), nullable=False)
    package_version = Column(String(50), nullable=False)
    started_at = Column(DateTime, nullable=True)
    expired_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default=ACTIVE_STATUS)
    is_sso_default = Column(Boolean, default=False, nullable=False)
