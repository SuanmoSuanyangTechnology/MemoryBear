from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class ModelUsageRecord(Base):
    """模型调用用量记录（spec §13.3）：计量 stream 消费落表，兼 least-used 选路状态真源。"""

    __tablename__ = "model_usage_records"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_id = Column(UUID(as_uuid=True), nullable=False)
    source_service = Column(String(32), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    config_id = Column(UUID(as_uuid=True), nullable=False)
    channel_id = Column(UUID(as_uuid=True), nullable=True)
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    provider = Column(String(50), nullable=False)
    model_name = Column(String(255), nullable=False)
    capability = Column(String(32), nullable=False)
    stream = Column(Boolean, nullable=False, default=False)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    images_count = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    error_type = Column(String(64), nullable=True)
    attempts = Column(SmallInteger, nullable=False, default=1)
    request_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_model_usage_records_event_id"),
        Index("ix_usage_tenant_time", "tenant_id", created_at.desc()),
        Index("ix_usage_channel_time", "channel_id", created_at.desc()),
        Index("ix_usage_model_time", "provider", "model_name", created_at.desc()),
        Index(
            "ix_usage_resource_time",
            "tenant_id",
            "resource_type",
            "resource_id",
            created_at.desc(),
        ),
    )
