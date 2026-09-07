from sqlalchemy import BigInteger, Column, Identity, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from src.models.base import ServiceBase


class AuditLog(ServiceBase):
    __tablename__ = "audit_logs"
    # 幂等键约束用命名形式（uq_audit_logs_event_id）与迁移一致：
    # 列级 unique=True 是匿名约束，autogenerate 对比时与迁移命名约束不匹配会误判 drop/create
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_audit_logs_event_id"),
    )

    id = Column(BigInteger, Identity(always=False), primary_key=True)
    event_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(Text, nullable=False)
    actor_id = Column(Text, nullable=True, index=True)
    tenant_id = Column(Text, nullable=True, index=True)
    target = Column(Text, nullable=True)
    result = Column(Text, nullable=False)
    detail = Column(JSONB, nullable=True)
    # ts 默认索引名 ix_audit_logs_ts 与迁移一致；索引支持设计 §7 的审计查询（时间范围/租户/actor）
    ts = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
