"""仅存储 PostgreSQL 事件元数据；节点文档保留在 Neo4j 中。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UUID,
    UniqueConstraint,
    text,
)

from app.db import Base
from app.core.memory.storage.enums import MemoryNodeType
from app.core.utils.datetime_utils import utcnow_naive


class OutboxEvent(Base):
    __tablename__ = "memory_storage_outbox_events"

    id = Column(UUID, primary_key=True)
    sequence = Column(BigInteger, Identity(), nullable=False)
    label = Column(String(64), nullable=False)
    node_id = Column(Text, nullable=False)
    operation = Column(String(20), nullable=False, server_default="upsert")
    status = Column(String(20), nullable=False, server_default="pending")
    attempt_count = Column(SmallInteger, nullable=False, server_default=text("0"))
    locked_by = Column(String(128))
    claim_token = Column(UUID)
    locked_at = Column(DateTime)
    heartbeat_at = Column(DateTime)
    last_error = Column(Text)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )
    processed_at = Column(DateTime)
    failed_at = Column(DateTime)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_memory_storage_outbox_events"),
        UniqueConstraint("sequence", name="uq_memory_outbox_sequence"),
        CheckConstraint(
            "label IN (" + ", ".join(repr(label.value) for label in MemoryNodeType) + ")",
            name="ck_memory_outbox_label",
        ),
        CheckConstraint(
            "operation IN ('upsert', 'delete', 'draft_delete')",
            name="ck_memory_outbox_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'failed')",
            name="ck_memory_outbox_status",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name="ck_memory_outbox_attempt_count",
        ),
        CheckConstraint(
            "length(label) > 0 AND length(node_id) > 0",
            name="ck_memory_outbox_node_identity",
        ),
        CheckConstraint(
            "(status = 'processing' AND locked_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND locked_at IS NOT NULL AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'processing' AND locked_by IS NULL AND claim_token IS NULL "
            "AND locked_at IS NULL AND heartbeat_at IS NULL)",
            name="ck_memory_outbox_claim_state",
        ),
        CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL AND failed_at IS NULL) OR "
            "(status = 'failed' AND failed_at IS NOT NULL AND processed_at IS NULL) OR "
            "(status IN ('pending', 'processing') AND processed_at IS NULL AND failed_at IS NULL)",
            name="ck_memory_outbox_terminal_times",
        ),
        Index(
            "ix_memory_outbox_pending",
            "sequence",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_memory_outbox_node", "label", "node_id", "sequence"),
        Index(
            "uq_memory_outbox_processing_node",
            "label",
            "node_id",
            unique=True,
            postgresql_where=text("status = 'processing'"),
        ),
        Index(
            "ix_memory_outbox_heartbeat",
            "heartbeat_at",
            postgresql_where=text("status = 'processing'"),
        ),
        Index(
            "ix_memory_outbox_processed_cleanup",
            "processed_at",
            postgresql_where=text("status = 'processed'"),
        ),
        Index(
            "ix_memory_outbox_failed_cleanup",
            "failed_at",
            postgresql_where=text("status = 'failed'"),
        ),
    )
