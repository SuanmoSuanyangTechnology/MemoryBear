"""resource_pack_versions / tenant_resource_packs 只读映射（premium 平台域表）。

配额叠加口径（与宿主 `ResourcePackRepository.get_overlay` 一致）：租户 active 且未过期的
资源包实例 ⋈ 其锁定版本快照，按实例 `tier_id` 命中的 tier `quota_grants[key] * quantity` 求和。
"""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..base import ReadOnlyBase


class ResourcePackVersion(ReadOnlyBase):
    __tablename__ = "resource_pack_versions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    version_snapshot = Column(JSONB, nullable=False)


class TenantResourcePack(ReadOnlyBase):
    __tablename__ = "tenant_resource_packs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    resource_pack_version_id = Column(UUID(as_uuid=True), nullable=False)
    tier_id = Column(UUID(as_uuid=True), nullable=False)
    quantity = Column(Integer, nullable=False, server_default="1")
    status = Column(String(20), nullable=False)
    expired_at = Column(DateTime, nullable=True)
